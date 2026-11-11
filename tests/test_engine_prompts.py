"""Claude Code in the chat: permission and AskUserQuestion prompts answered in
the app, the CLI's own tool calls and skills, /add-dir, slash commands.

No CLI is spawned: `_drive` / `dispatch.run` are faked, as in test_engines.py.
"""
import asyncio
import json
import threading
from types import SimpleNamespace

from test_engines import _rpc, _toolset, _turn

from pcb_assistant.engines import config as engine_config
from pcb_assistant.engines import mcp_bridge
from pcb_assistant.engines.base import EngineResult, Usage


# --- bridge ---

def test_bridge_serves_the_permission_tool_only_when_asked():
    with mcp_bridge.serve(_toolset()) as bridge:
        _, listed = _rpc(bridge, "tools/list")
        assert mcp_bridge.PERMISSION_TOOL not in {t["name"] for t in listed["result"]["tools"]}

    asked = []

    def permission(args):
        asked.append(args)
        return {"behavior": "allow", "updatedInput": args["input"]}

    with mcp_bridge.serve(_toolset(), max_calls=0, permission=permission) as bridge:
        _, listed = _rpc(bridge, "tools/list")
        assert mcp_bridge.PERMISSION_TOOL in {t["name"] for t in listed["result"]["tools"]}
        _, out = _rpc(bridge, "tools/call", {"name": mcp_bridge.PERMISSION_TOOL,
                                             "arguments": {"tool_name": "Read", "input": {"file_path": "x"}}})
        reply = json.loads(out["result"]["content"][0]["text"])
        assert reply == {"behavior": "allow", "updatedInput": {"file_path": "x"}}
        assert bridge.calls == 0 and asked  # a decision isn't a tool call, and dodges the step limit


def test_bridge_permission_failure_denies():
    def broken(args):
        raise RuntimeError("boom")

    with mcp_bridge.serve(_toolset(), permission=broken) as bridge:
        _, out = _rpc(bridge, "tools/call", {"name": mcp_bridge.PERMISSION_TOOL,
                                             "arguments": {"tool_name": "Bash", "input": {}}})
        assert json.loads(out["result"]["content"][0]["text"])["behavior"] == "deny"


# --- Claude Code engine ---

def test_claude_decide_maps_answers_to_the_cli_reply():
    from pcb_assistant.engines.claude_code import decide

    q = {"questions": [{"question": "Which LDO?", "header": "LDO", "options": [{"label": "AP2112"}],
                        "multiSelect": False}]}
    seen = []

    def ask(payload):
        seen.append(payload)
        return {"answers": {"Which LDO?": "AP2112"}}

    out = decide(ask, {"tool_name": "AskUserQuestion", "input": q})
    assert seen[0]["kind"] == "question" and seen[0]["questions"] == q["questions"]
    assert out == {"behavior": "allow", "updatedInput": {**q, "answers": {"Which LDO?": "AP2112"}}}
    assert decide(lambda p: None, {"tool_name": "AskUserQuestion", "input": q})["behavior"] == "deny"

    read = {"tool_name": "Read", "input": {"file_path": "C:/x.txt"}}
    assert decide(lambda p: {"allow": True}, read) == {"behavior": "allow", "updatedInput": {"file_path": "C:/x.txt"}}
    assert decide(lambda p: {"allow": False, "message": "not that"}, read) == {"behavior": "deny", "message": "not that"}
    assert "didn't answer" in decide(lambda p: None, read)["message"]


def test_claude_stream_parser_reports_builtin_tools_and_commands_in_the_chat():
    from pcb_assistant.engines.claude_code import StreamParser

    init = {"type": "system", "subtype": "init", "session_id": "s",
            "slash_commands": ["tdd", "compact"], "skills": ["tdd"]}
    use = {"type": "assistant", "parent_tool_use_id": None, "message": {"id": "m1", "content": [
        {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "tdd"}},
        {"type": "tool_use", "id": "t2", "name": "mcp__pcb__get_bom", "input": {}},
        {"type": "tool_use", "id": "t3", "name": "AskUserQuestion", "input": {}},
    ]}}
    res = {"type": "user", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "Launching skill"}]},
    ]}}
    lines = [json.dumps(x) for x in (init, use, use, res)]  # partial messages repeat a message

    events = []
    p = StreamParser(events.append, interactive=True)
    for line in lines:
        p.feed(line)
    assert events == [
        {"type": "commands", "commands": ["tdd", "compact"], "skills": ["tdd"]},
        {"type": "tool_call", "name": "skill tdd", "args": {"skill": "tdd"}},
        {"type": "tool_result", "name": "skill tdd", "summary": "Launching skill"},
    ]

    quiet = []
    p = StreamParser(quiet.append)  # reviewers and parts research: text frames only
    for line in lines:
        p.feed(line)
    assert quiet == []


def _claude_run(monkeypatch, **run_kw):
    from pcb_assistant.engines import claude_code

    seen = {}

    def drive(args, prompt, cwd, on_event, timeout, cancel=None, interactive=False):
        system = args[args.index("--system-prompt-file") + 1]
        with open(system, encoding="utf-8") as f:
            seen.update(args=args, cwd=cwd, interactive=interactive, system=f.read())
        return EngineResult(text="ok")

    monkeypatch.setattr(claude_code, "cli_path", lambda: "claude")
    monkeypatch.setattr(claude_code, "_drive", drive)
    claude_code.run("sonnet", "SYS", "hi", toolset=_toolset(), **run_kw)
    return seen


def _flag(args, name):
    return args[args.index(name) + 1] if name in args else None


def test_claude_chat_run_routes_prompts_to_the_app(tmp_path, monkeypatch):
    engine_config.set_all({"engines": {"claude": {"enabled": True, "tos_ack": True}}})
    try:
        extra = tmp_path / "extra"
        extra.mkdir()
        seen = _claude_run(monkeypatch, ask=lambda p: None, project_dir=tmp_path,
                           add_dirs=[str(extra), str(tmp_path / "gone")])
        args = seen["args"]
        assert seen["cwd"] == tmp_path and seen["interactive"]
        assert _flag(args, "--permission-prompt-tool") == "mcp__pcb__permission_prompt"
        assert _flag(args, "--permission-mode") == "default"
        assert _flag(args, "--tools") == "Read,Glob,Grep,AskUserQuestion,Skill"
        assert _flag(args, "--setting-sources") == "user"
        assert json.loads(_flag(args, "--settings")) == {"disableAllHooks": True}
        assert "--disable-slash-commands" not in args
        added = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
        assert str(extra) in added and len(added) == 2  # + the run's temp dir; a missing folder is dropped
        assert "Running in Ohmen through Claude Code" in seen["system"]

        engine_config.set_all({"engines": {"claude": {"enabled": True, "tos_ack": True,
                                                      "user_setup": False, "full_tools": True}}})
        args = _claude_run(monkeypatch, ask=lambda p: None, project_dir=tmp_path)["args"]
        assert _flag(args, "--tools") == "default"
        assert _flag(args, "--setting-sources") == "" and "--disable-slash-commands" in args
    finally:
        engine_config.reset()


def test_claude_non_chat_run_stays_isolated(tmp_path, monkeypatch):
    seen = _claude_run(monkeypatch)
    args = seen["args"]
    assert "--permission-prompt-tool" not in args and "--add-dir" not in args
    assert _flag(args, "--tools") == "Read" and _flag(args, "--setting-sources") == ""
    assert "--disable-slash-commands" in args and seen["cwd"] != tmp_path
    assert "Running in Ohmen" not in seen["system"]


def test_prompt_wait_ends_on_answer_and_on_cancel():
    from pcb_assistant.engines import prompts

    p = prompts.open_prompt({"kind": "permission"})
    threading.Timer(0.1, lambda: prompts.answer(p.id, {"allow": True})).start()
    assert prompts.wait(p) == {"allow": True}
    assert not prompts.is_pending(p.id) and not prompts.answer(p.id, {"allow": False})

    cancel = threading.Event()
    cancel.set()
    assert prompts.wait(prompts.open_prompt({"kind": "question"}), cancel) is None


# --- the chat turn ---

def _prompting_engine(calls, payloads):
    def run(model_spec, system, prompt, *, on_event=None, on_tool=None, ask=None, **kw):
        calls.append({"prompt": prompt, "system": system, "ask": ask, **kw})
        answers = [ask(p) for p in payloads] if ask else []
        on_event({"type": "token", "text": json.dumps(answers)})
        return EngineResult(text="", session_id="cli-1", usage=Usage(requests=1))
    return run


def _turn_answering(tmp_path, message, spec, answer, chat_id=None):
    from pcb_assistant.agent import stream as st
    from pcb_assistant.engines import prompts

    async def go():
        state = SimpleNamespace(project=tmp_path, design=None)
        out = []
        async for f in st.run_agent_stream(state, message, spec, chat_id):
            out.append(f)
            if f["type"] == "prompt":
                assert prompts.answer(f["id"], answer(f))
        return out

    return asyncio.run(go())


def test_engine_chat_prompt_round_trip(tmp_path, monkeypatch):
    from pcb_assistant.agent.chats import ChatStore
    from pcb_assistant.engines import dispatch

    calls = []
    read = {"kind": "permission", "tool": "Read", "input": {"file_path": "C:/notes.txt"}}
    question = {"kind": "question", "tool": "AskUserQuestion", "questions": [{"question": "Which?", "options": []}]}
    monkeypatch.setattr(dispatch, "run", _prompting_engine(calls, [read, question, read]))

    def answer(frame):
        return {"allow": True, "always": True} if frame["kind"] == "permission" else {"answers": {"Which?": "B"}}

    frames = _turn_answering(tmp_path, "look", "claude-sub:sonnet", answer)
    assert [f["kind"] for f in frames if f["type"] == "prompt"] == ["permission", "question"]  # 2nd Read: allowed for the chat
    assert json.loads(next(f["text"] for f in frames if f["type"] == "token")) == [
        {"allow": True, "always": True}, {"answers": {"Which?": "B"}}, {"allow": True}]
    assert [f["outcome"] for f in frames if f["type"] == "prompt_done"] == ["allowed for this chat", "answered: B"]
    assert calls[0]["project_dir"] == tmp_path

    sid = frames[0]["chat_id"]
    trace = ChatStore.for_project(tmp_path).get(sid)["transcript"][-1]["tools"]
    assert {"kind": "result", "name": "permission: Read notes.txt", "info": "allowed for this chat"} in trace
    assert {"kind": "result", "name": "asked: Which?", "info": "answered: B"} in trace


def test_codex_chat_gets_no_prompt_options(tmp_path, monkeypatch):
    from pcb_assistant.engines import dispatch

    calls = []
    monkeypatch.setattr(dispatch, "run", _prompting_engine(calls, []))
    _turn(tmp_path, "hi", "codex-sub:gpt-5.1")
    assert calls[0]["ask"] is None and "add_dirs" not in calls[0]


def test_add_dir_is_kept_on_the_chat_and_passed_to_later_runs(tmp_path, monkeypatch):
    from pcb_assistant.engines import dispatch

    calls = []
    monkeypatch.setattr(dispatch, "run", _prompting_engine(calls, []))
    lib = tmp_path / "libs"
    lib.mkdir()
    frames = _turn(tmp_path, "/add-dir libs", "claude-sub:sonnet")
    assert not calls  # handled without the model
    assert "Added" in next(f["text"] for f in frames if f["type"] == "token")
    sid = frames[0]["chat_id"]

    frames = _turn(tmp_path, "/add-dir nowhere", "claude-sub:sonnet", sid)
    assert "isn't a folder" in next(f["text"] for f in frames if f["type"] == "token")

    _turn(tmp_path, "read the lib", "claude-sub:sonnet", sid)
    assert calls[0]["add_dirs"] == [str(lib.resolve())]


def test_slash_command_stays_first_in_a_fresh_seeded_session(tmp_path, monkeypatch):
    from pcb_assistant.engines import dispatch

    calls = []
    monkeypatch.setattr(dispatch, "run", _prompting_engine(calls, []))
    sid = _turn(tmp_path, "hello", "claude-sub:sonnet")[0]["chat_id"]
    _turn(tmp_path, "/tdd plan it", "claude-sub:opus", sid)  # model switch: a fresh session, seeded
    assert calls[1]["prompt"] == "/tdd plan it"
    assert "Earlier in this conversation:" in calls[1]["system"]
