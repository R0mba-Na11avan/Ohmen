"""Subscription engines: MCP bridge, CLI stream parsing, routing, chat + review paths.

No CLI is spawned here — engine runs are faked at `dispatch.run`; the parsers
are fed recorded/protocol-shaped lines.
"""
import asyncio
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
from pydantic_ai.toolsets import FunctionToolset

from pcb_assistant.engines import config as engine_config
from pcb_assistant.engines import mcp_bridge
from pcb_assistant.engines.base import EngineError, EngineResult, Usage, UsageLimitError


def _toolset():
    ts = FunctionToolset()

    @ts.tool_plain
    def add(a: int, b: int = 1) -> str:
        """Add two numbers."""
        return str(a + b)

    @ts.tool_plain
    def pages() -> object:
        """Return datasheet pages as a PDF."""
        from pydantic_ai import BinaryContent, ToolReturn

        return ToolReturn(return_value="Attached p.3.", content=[BinaryContent(data=b"%PDF-1", media_type="application/pdf")])

    return ts


def _rpc(bridge, method, params=None, token=None, msg_id=1):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        body["id"] = msg_id
    req = urllib.request.Request(
        bridge.url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token or bridge.token}"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else None)


# --- MCP bridge ---

def test_bridge_lists_and_calls_tools_and_reports_them(tmp_path):
    events = []
    with mcp_bridge.serve(_toolset(), events.append, attach_dir=tmp_path) as bridge:
        assert bridge.url.startswith("http://127.0.0.1:")
        _, init = _rpc(bridge, "initialize", {"protocolVersion": "2025-06-18"})
        assert init["result"]["capabilities"]["tools"] is not None
        assert _rpc(bridge, "notifications/initialized", msg_id=None) == (202, None)

        _, listed = _rpc(bridge, "tools/list")
        tools = {t["name"]: t for t in listed["result"]["tools"]}
        assert tools["add"]["inputSchema"]["required"] == ["a"]

        _, called = _rpc(bridge, "tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}})
        assert called["result"] == {"content": [{"type": "text", "text": "5"}], "isError": False}

        _, bad = _rpc(bridge, "tools/call", {"name": "add", "arguments": {"a": "x"}})
        assert bad["result"]["isError"]

        _, pdf = _rpc(bridge, "tools/call", {"name": "pages", "arguments": {}})
        texts = [c["text"] for c in pdf["result"]["content"]]
        assert texts[0] == "Attached p.3." and "Read tool" in texts[1]
        assert (tmp_path / "pages-1.pdf").read_bytes() == b"%PDF-1"

    assert events[0] == {"type": "tool_call", "name": "add", "args": {"a": 2, "b": 3}}
    assert events[1] == {"type": "tool_result", "name": "add", "summary": "5"}


def test_bridge_rejects_wrong_token_and_finished_runs():
    with mcp_bridge.serve(_toolset()) as bridge:
        with pytest.raises(urllib.error.HTTPError) as e:
            _rpc(bridge, "tools/list", token="nope")
        assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError):  # run over: its tools are gone
        _rpc(bridge, "tools/list")


def test_bridge_step_limit():
    with mcp_bridge.serve(_toolset(), max_calls=1) as bridge:
        _rpc(bridge, "tools/call", {"name": "add", "arguments": {"a": 1}})
        _, over = _rpc(bridge, "tools/call", {"name": "add", "arguments": {"a": 1}})
        assert over["result"]["isError"] and "Step limit" in over["result"]["content"][0]["text"]
        assert bridge.limited


def test_bridge_pdf_without_attach_dir_becomes_a_note():
    with mcp_bridge.serve(_toolset()) as bridge:
        _, pdf = _rpc(bridge, "tools/call", {"name": "pages", "arguments": {}})
        assert "read the pages as text" in pdf["result"]["content"][1]["text"]


# --- Claude Code stream-json ---

def _claude_lines(text_deltas, rate_status="allowed", result_extra=None):
    sid = "s-1"
    lines = [{"type": "system", "subtype": "init", "session_id": sid}]
    lines.append({"type": "stream_event", "parent_tool_use_id": None,
                  "event": {"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
    for t in text_deltas:
        lines.append({"type": "stream_event", "parent_tool_use_id": None,
                      "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": t}}})
    lines.append({"type": "rate_limit_event", "rate_limit_info": {
        "status": rate_status, "resetsAt": 1791622800, "rateLimitType": "five_hour",
        "unifiedWindows": {"five_hour": {"utilization": 0.08, "resetsAt": 1791622800}}}})
    result = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "session_id": sid,
              "result": "".join(text_deltas),
              "usage": {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50,
                        "output_tokens": 10}}
    result.update(result_extra or {})
    lines.append(result)
    return [json.dumps(x) for x in lines]


def test_claude_stream_parser():
    from pcb_assistant.engines.claude_code import StreamParser

    events = []
    p = StreamParser(events.append)
    for line in _claude_lines(["The code ", "is 7Q-33."]):
        p.feed(line)
    r = p.finish(0)
    assert r.text == "The code is 7Q-33." and r.session_id == "s-1"
    assert r.usage == Usage(requests=3, tokens_in=152, tokens_cached=50, tokens_out=10, tokens_cache_write=100)
    assert [e["type"] for e in events] == ["thinking", "token", "token", "rate_limit"]
    assert events[-1]["utilization"] == 0.08


def test_claude_stream_parser_tracks_the_largest_request():
    from pcb_assistant.engines.claude_code import StreamParser

    def assistant(mid, fresh, write, read, parent=None):
        return json.dumps({"type": "assistant", "parent_tool_use_id": parent, "message": {
            "id": mid, "usage": {"input_tokens": fresh, "cache_creation_input_tokens": write,
                                 "cache_read_input_tokens": read, "output_tokens": 5}}})

    p = StreamParser()
    lines = _claude_lines(["ok"])
    # Partial messages repeat a message per content block; a subagent's don't count.
    extra = [assistant("m1", 10, 9000, 0), assistant("m1", 10, 9000, 0),
             assistant("m2", 4, 300, 9000), assistant("m3", 1, 1, 99999, parent="tool-1")]
    for line in lines[:-1] + extra + lines[-1:]:
        p.feed(line)
    assert p.finish(0).usage.max_context == 9304


def test_usage_log_splits_fresh_input(tmp_path):
    from pcb_assistant.agent import usage_log

    entry = usage_log.record(tmp_path, "chat", Usage(requests=2, tokens_in=1000, tokens_cached=700,
                                                      tokens_cache_write=200, tokens_out=50, max_context=600),
                             route="subscription", effort=None)
    assert (entry["tokens_fresh"], entry["tokens_cache_write"], entry["tokens_cached"]) == (100, 200, 700)
    assert "effort" not in entry
    line = (tmp_path / ".pcb_assistant" / "usage.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line)["route"] == "subscription"


def test_claude_usage_limit_is_its_own_error():
    from pcb_assistant.engines.claude_code import StreamParser

    p = StreamParser()
    for line in _claude_lines([], rate_status="rejected",
                              result_extra={"is_error": True, "result": "You've hit your usage limit"}):
        p.feed(line)
    with pytest.raises(UsageLimitError) as e:
        p.finish(1)
    assert e.value.resets_at == 1791622800


# --- Codex app-server ---

def test_codex_turn_parser():
    from pcb_assistant.engines.codex import TurnParser

    events = []
    p = TurnParser(events.append)
    p.thread_id = "t-1"
    for msg in [
        {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "checking"}},
        {"method": "item/agentMessage/delta", "params": {"delta": "EN is "}},
        {"method": "item/agentMessage/delta", "params": {"delta": "floating."}},
        {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": {
            "inputTokens": 900, "cachedInputTokens": 400, "outputTokens": 30}}}},
        {"method": "account/rateLimits/updated", "params": {"rateLimits": {"primary": {
            "usedPercent": 42, "windowDurationMins": 300, "resetsAt": 1791622800}}}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ]:
        p.feed(msg)
    assert p.done
    r = p.finish()
    assert r.text == "EN is floating." and r.session_id == "t-1"
    assert (r.usage.tokens_in, r.usage.tokens_cached, r.usage.tokens_out) == (900, 400, 30)
    assert r.rate_limit["utilization"] == 0.42
    assert [e["type"] for e in events] == ["thinking", "token", "token", "rate_limit"]


def test_codex_commentary_is_thinking_not_answer():
    from pcb_assistant.engines.codex import TurnParser

    events = []
    p = TurnParser(events.append)
    for msg in [
        {"method": "item/started", "params": {"item": {"type": "agentMessage", "id": "c1", "phase": "commentary"}}},
        {"method": "item/agentMessage/delta", "params": {"itemId": "c1", "delta": "I'll check U2."}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "c1", "phase": "commentary", "text": "I'll check U2."}}},
        {"method": "item/started", "params": {"item": {"type": "agentMessage", "id": "f1", "phase": "final_answer"}}},
        {"method": "item/agentMessage/delta", "params": {"itemId": "f1", "delta": "U2 is fine."}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ]:
        p.feed(msg)
    assert p.finish().text == "U2 is fine."
    assert "".join(e["text"] for e in events if e["type"] == "thinking").strip() == "I'll check U2."


def test_codex_only_approves_our_own_mcp_server(tmp_path):
    from pcb_assistant.engines.codex import AppServer

    s = AppServer.__new__(AppServer)
    s.trusted_server = "pcb"
    assert s.answer("mcpServer/elicitation/request", {"serverName": "pcb"})["action"] == "accept"
    assert s.answer("mcpServer/elicitation/request", {"serverName": "node_repl"}) == {"action": "decline"}
    assert s.answer("item/commandExecution/requestApproval", {}) == {"decision": "decline"}
    assert s.answer("execCommandApproval", {}) == {"decision": "denied"}


def test_codex_overrides_lock_down_and_drop_user_mcp_servers(tmp_path, monkeypatch):
    from pcb_assistant.engines import codex

    (tmp_path / "config.toml").write_text('[mcp_servers.node_repl]\ncommand = "node"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    bridge = SimpleNamespace(name="pcb", url="http://127.0.0.1:9/mcp/x", token="t")
    out = codex.run_overrides(bridge)
    assert 'sandbox_mode="read-only"' in out and 'approval_policy="never"' in out
    assert "features.shell_tool=false" in out and "features.code_mode_host=false" not in out
    assert "mcp_servers.node_repl.enabled=false" in out
    assert 'mcp_servers.pcb.default_tools_approval_mode="approve"' in out
    assert 'mcp_servers.pcb.http_headers={Authorization="Bearer t"}' in out


def test_codex_found_in_the_desktop_app(tmp_path, monkeypatch):
    from pcb_assistant.engines import codex

    exe = tmp_path / "codex.exe"
    exe.write_text("")
    monkeypatch.setattr(codex, "find_cli", lambda names, override: None)
    monkeypatch.setattr(codex, "_APP_BUNDLED", [tmp_path / "missing", exe])
    assert codex.cli_path() == str(exe)


def test_codex_usage_limit():
    from pcb_assistant.engines.codex import TurnParser

    p = TurnParser()
    p.feed({"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {
        "message": "You've hit your usage limit.", "codexErrorInfo": "usageLimitExceeded"}}}})
    with pytest.raises(UsageLimitError):
        p.finish()


# --- settings + routing ---

def test_claude_engine_needs_the_tos_acknowledgement():
    engine_config.set_all({"engines": {"claude": {"enabled": True}, "codex": {"enabled": True}}})
    assert engine_config.enabled("codex") and not engine_config.enabled("claude")
    engine_config.set_all({"engines": {"claude": {"enabled": True, "tos_ack": True}}})
    assert engine_config.enabled("claude")


def test_picker_merges_routes_and_follows_preferred_route(fake_listers, monkeypatch):
    from pcb_assistant.providers import credentials, registry

    fake_listers(openai={"gpt-5.1": {}, "gpt-4o": {}})
    credentials.set_all({"openai": {"api_key": "k"}})
    monkeypatch.setattr("pcb_assistant.engines.codex.list_models", lambda: ["gpt-5.1", "gpt-5.1-codex"])

    engine_config.set_all({"engines": {"codex": {"enabled": True}}, "preferred_route": {"openai": "sub"}})
    rows = {r.cap.model: r for r in registry.picker_models()}
    assert rows["gpt-5.1"].routes == {"api": "openai:gpt-5.1", "sub": "codex-sub:gpt-5.1"}
    assert rows["gpt-5.1"].cap.spec == "codex-sub:gpt-5.1"  # preferred route wins
    assert rows["gpt-4o"].cap.spec == "openai:gpt-4o"  # only the API serves it
    assert rows["gpt-5.1-codex"].routes == {"sub": "codex-sub:gpt-5.1-codex"}
    assert rows["gpt-5.1"].cap.recommended

    engine_config.set_all({"engines": {"codex": {"enabled": True}}, "preferred_route": {"openai": "api"}})
    registry.clear_sub_models()
    rows = {r.cap.model: r for r in registry.picker_models()}
    assert rows["gpt-5.1"].cap.spec == "openai:gpt-5.1"


def test_disabled_engine_adds_no_models(fake_listers):
    from pcb_assistant.providers import registry

    assert registry.picker_models() == []
    engine_config.set_all({"engines": {"claude": {"enabled": True}}})  # no ToS ack
    assert registry.picker_models() == []


def test_claude_sub_uses_aliases_without_an_api_listing():
    from pcb_assistant.providers import registry

    engine_config.set_all({"engines": {"claude": {"enabled": True, "tos_ack": True}}})
    rows = {r.cap.model: r for r in registry.picker_models()}
    assert set(rows) == {"opus", "sonnet", "haiku"}
    assert rows["sonnet"].cap.recommended and rows["sonnet"].cap.cost_tier == "subscription"


def test_recommended_is_the_newest_in_family():
    from pcb_assistant.providers.registry import capability, mark_recommended

    caps = mark_recommended([capability(f"claude:{m}") for m in
                             ("claude-sonnet-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5")])
    assert [c.model for c in caps if c.recommended] == ["claude-sonnet-5-5"]


def test_api_fallback_only_when_opted_in_and_keyed():
    from pcb_assistant.providers import credentials
    from pcb_assistant.providers.registry import api_fallback

    engine_config.set_all({"engines": {"codex": {"enabled": True}}})
    assert api_fallback("codex-sub:gpt-5.1") is None  # not opted in
    engine_config.set_all({"engines": {"codex": {"enabled": True}}, "fallback_to_api": True})
    assert api_fallback("codex-sub:gpt-5.1") is None  # no key
    credentials.set_all({"openai": {"api_key": "k"}})
    assert api_fallback("codex-sub:gpt-5.1") == "openai:gpt-5.1"
    assert api_fallback("openai:gpt-5.1") is None


def test_dispatch_refuses_a_disabled_engine():
    from pcb_assistant.engines import dispatch
    from pcb_assistant.engines.base import EngineError

    with pytest.raises(EngineError, match="Settings"):
        dispatch.run("codex-sub:gpt-5.1", "sys", "hi")


def test_subscription_reviewers_cap_at_three_at_once():
    from pcb_assistant.reasoning.review_team import max_parallel

    assert max_parallel("claude:claude-sonnet-5", 6) == 6
    assert max_parallel("codex-sub:gpt-5.1", 6) == 3
    assert max_parallel("codex-sub:gpt-5.1", 2) == 2


# --- chat turn on an engine ---

def _fake_engine(calls, answer="Done.", fail=None):
    def run(model_spec, system, prompt, *, toolset=None, on_event=None, on_tool=None, session=None, **kw):
        calls.append({"spec": model_spec, "prompt": prompt, "session": session, "effort": kw.get("effort")})
        if fail:
            raise fail
        on_event({"type": "thinking", "text": "looking"})
        on_tool({"type": "tool_call", "name": "fill_bom_from_schematic", "args": {}})
        on_tool({"type": "tool_result", "name": "fill_bom_from_schematic", "summary": "No design loaded."})
        on_event({"type": "token", "text": answer})
        on_event({"type": "rate_limit", "utilization": 0.1, "resets_at": None, "window": "five_hour"})
        return EngineResult(text=answer, session_id=f"cli-{len(calls)}", usage=Usage(requests=1))
    return run


def _turn(tmp_path, message, spec, chat_id=None, effort="high"):
    from pcb_assistant.agent import stream as st

    async def go():
        state = SimpleNamespace(project=tmp_path, design=None)
        return [f async for f in st.run_agent_stream(state, message, spec, chat_id, effort=effort)]

    return asyncio.run(go())


def test_engine_chat_turn_streams_frames_and_resumes_its_session(tmp_path, monkeypatch):
    from pcb_assistant.agent.chats import ChatStore
    from pcb_assistant.engines import dispatch

    calls = []
    monkeypatch.setattr(dispatch, "run", _fake_engine(calls))
    frames = _turn(tmp_path, "what's on the BOM?", "codex-sub:gpt-5.1")
    kinds = [f["type"] for f in frames]
    assert kinds[:1] == ["session"] and kinds[-1] == "done"
    assert {"thinking", "tool_call", "tool_result", "bom", "token", "rate_limit"} <= set(kinds)

    sid = frames[0]["chat_id"]
    store = ChatStore.for_project(tmp_path)
    assert store.engine_session(sid) == {"provider": "codex-sub", "model": "gpt-5.1", "id": "cli-1"}
    turn = store.get(sid)["transcript"][-1]
    assert turn["a"] == "Done." and turn["route"] == "subscription"
    assert len(store.messages(sid)) == 2  # prompt + answer, for a later API turn

    _turn(tmp_path, "and now?", "codex-sub:gpt-5.1", sid)
    assert calls[1]["session"] == "cli-1" and calls[1]["prompt"] == "and now?"

    _turn(tmp_path, "switch", "codex-sub:gpt-5.1-codex", sid)  # model changed: fresh, seeded
    assert calls[2]["session"] is None
    assert calls[2]["prompt"].startswith("Earlier in this conversation:")
    assert "User: and now?" in calls[2]["prompt"]


def test_engine_chat_turn_carries_effort(tmp_path, monkeypatch):
    from pcb_assistant.engines import dispatch

    calls = []
    monkeypatch.setattr(dispatch, "run", _fake_engine(calls))
    _turn(tmp_path, "hi", "codex-sub:gpt-5.1", effort="max")
    _turn(tmp_path, "hi", "codex-sub:gpt-5.1", effort="low")
    assert [c["effort"] for c in calls] == ["xhigh", "low"]


def test_kill_on_cancel_stops_the_cli():
    import subprocess
    import sys
    import threading
    import time

    from pcb_assistant.engines.base import kill_on_cancel

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    cancel = threading.Event()
    kill_on_cancel(proc, cancel)
    t = time.time()
    cancel.set()
    proc.wait(timeout=5)
    assert time.time() - t < 5


def test_engine_chat_stop_cancels_the_cli(tmp_path, monkeypatch):
    """Closing the turn early (Stop) sets the engine's cancel event."""
    import threading

    from pcb_assistant.agent import stream as st
    from pcb_assistant.engines import dispatch

    seen: list[threading.Event] = []
    started = threading.Event()

    def run(model_spec, system, prompt, *, on_event=None, cancel=None, **kw):
        seen.append(cancel)
        on_event({"type": "token", "text": "partial"})
        started.set()
        cancel.wait(5)  # the CLI, until it is killed
        raise EngineError("killed")

    monkeypatch.setattr(dispatch, "run", run)

    async def go():
        state = SimpleNamespace(project=tmp_path, design=None)
        gen = st.run_agent_stream(state, "hi", "codex-sub:gpt-5.1")
        async for f in gen:
            if f["type"] == "token":
                break
        await gen.aclose()

    asyncio.run(go())
    assert seen and seen[0].is_set()

    # What arrived before the stop is kept, marked as stopped.
    from pcb_assistant.agent.chats import ChatStore

    store = ChatStore.for_project(tmp_path)
    turn = store.get(store.summaries()[0]["id"])["transcript"][-1]
    assert turn["a"] == "partial" and turn["stopped"] is True


def test_engine_chat_usage_limit_error_frame(tmp_path, monkeypatch):
    from pcb_assistant.engines import dispatch

    monkeypatch.setattr(dispatch, "run", _fake_engine([], fail=UsageLimitError("spent", resets_at=123)))
    frames = _turn(tmp_path, "hi", "codex-sub:gpt-5.1")
    err = frames[-1]
    assert err["type"] == "error" and err["code"] == "usage_limit" and err["resets_at"] == 123


def test_reviewer_on_engine_keeps_findings_when_usage_runs_out(design, tmp_path, monkeypatch):
    from pcb_assistant.engines import dispatch
    from pcb_assistant.reasoning.reviewer_agent import run_reviewer

    calls = []

    def run(model_spec, system, prompt, **kw):
        calls.append(prompt)
        if len(calls) == 1:
            return EngineResult(text="ok", usage=Usage(requests=2, tokens_in=10))
        raise UsageLimitError("spent")

    monkeypatch.setattr(dispatch, "run", run)
    seen = []
    with pytest.raises(UsageLimitError):  # nothing found yet: the reviewer fails outright
        run_reviewer(design, "codex-sub:gpt-5.1", "board", "whole board", tmp_path,
                     progress=seen.append, refs=["U1", "U2"])
    assert calls == ["Audit U1.", "Audit U2."]
    assert any(e.get("status") == "usage" and e["requests"] == 2 for e in seen)


def test_linux_login_picks_first_installed_terminal(monkeypatch):
    from pcb_assistant.engines import login

    installed = {"ptyxis": "/usr/bin/ptyxis", "xterm": "/usr/bin/xterm"}
    monkeypatch.setattr(login.shutil, "which", installed.get)
    argv = login._linux_terminal(["codex", "login"])
    assert argv[:3] == ["/usr/bin/ptyxis", "--new-window", "--"]
    assert argv[3:5] == ["sh", "-c"] and argv[5].startswith("codex login;")

    monkeypatch.setattr(login.shutil, "which", lambda _name: None)
    with pytest.raises(OSError, match="codex login"):
        login._linux_terminal(["codex", "login"])
