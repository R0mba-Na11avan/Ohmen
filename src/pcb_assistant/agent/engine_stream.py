"""The chat turn on a subscription CLI — same frames as the pydantic-ai path.

The CLI runs the model loop; our chat tools reach it through the MCP bridge,
which reports each call, so the UI sees tool_call / tool_result frames (and the
BOM / findings / diagram follow-ups) exactly as it does for an API model.

Continuity: the chat remembers the CLI's session id and resumes it while the
provider and model stay the same. Otherwise (model or route switched, or the
session is gone) a fresh session starts, seeded with a compact transcript of
the chat so far.

Prompts (Claude Code): a permission check or an AskUserQuestion reaches `ask`
on a bridge thread, goes out as a `prompt` frame, and blocks until the user
answers (POST /chat/prompts/{id}); a `prompt_done` frame follows either way.
`/add-dir <folder>` is handled here, not by the model: the folder is kept on the
chat and passed to every later run as `--add-dir`. Other `/commands` go to the
CLI as typed, so the user's own skills run.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator

from pcb_assistant.agent.chat_images import image_path
from pcb_assistant.agent.chats import ChatStore
from pcb_assistant.agent.condense import seed as condense_seed
from pcb_assistant.agent.stream import follow_up_frames, merge_progress, record_stopped

SEED_TURNS = 12
SEED_CHARS_PER_ANSWER = 1500
ADD_DIR = "/add-dir"

# chat id -> tools the user allowed for the rest of that chat (until the app restarts)
_always_allowed: dict[str, set[str]] = {}


def prompt_label(payload: dict[str, Any]) -> str:
    """The trace line a prompt leaves in the chat history."""
    from pcb_assistant.engines.claude_code import tool_label

    if payload.get("kind") == "question":
        qs = [str(q.get("question", "")) for q in payload.get("questions") or [] if isinstance(q, dict)]
        return "asked: " + " / ".join(qs)[:120]
    return "permission: " + tool_label(str(payload.get("tool", "")), payload.get("input") or {})


def prompt_outcome(payload: dict[str, Any], ans: dict[str, Any] | None) -> str:
    if ans is None:
        return "no answer" if payload.get("kind") == "question" else "no answer — denied"
    if payload.get("kind") == "question":
        answers = ans.get("answers") if isinstance(ans.get("answers"), dict) else {}
        return ("answered: " + "; ".join(str(v) for v in answers.values())) if answers else "skipped"
    if ans.get("allow"):
        return "allowed for this chat" if ans.get("always") else "allowed"
    return "denied" + (f": {ans['message']}" if ans.get("message") else "")


def add_dir_reply(chats: ChatStore | None, sid: str | None, project, arg: str) -> str:
    """Answer `/add-dir [folder]` (relative folders resolve against the project)."""
    if not (chats and sid):
        return "Open a project first: added folders belong to a chat."
    arg = arg.strip().strip('"').strip("'")
    if not arg:
        dirs = chats.added_dirs(sid)
        listed = "\n".join(f"- `{d}`" for d in dirs) if dirs else "none yet"
        return f"Folders Claude Code can read in this chat besides the project:\n{listed}\n\nAdd one with `/add-dir <folder>`."
    from pathlib import Path

    path = Path(arg).expanduser()
    if not path.is_absolute() and project is not None:
        path = Path(project) / path
    path = path.resolve()
    if not path.is_dir():
        return f"`{path}` isn't a folder, so nothing was added."
    chats.add_dir(sid, str(path))
    return (f"Added `{path}`. Claude Code can now read files there for the rest of this chat "
            "(writes still ask first).")


def seed_transcript(transcript: list[dict[str, Any]]) -> str:
    """The recent turns of a chat as plain text, to start a fresh CLI session."""
    lines = []
    for t in transcript[-SEED_TURNS:]:
        if t.get("error") and not t.get("a"):
            continue
        a = (t.get("a") or "").strip()
        if len(a) > SEED_CHARS_PER_ANSWER:
            a = a[:SEED_CHARS_PER_ANSWER] + " […]"
        lines.append(f"User: {t.get('q', '').strip()}\nAssistant: {a}")
    if not lines:
        return ""
    return "Earlier in this conversation:\n\n" + "\n\n".join(lines) + "\n\n---\n\n"


def append_messages(prior_json: list[Any], message: str, answer: str, model_spec: str) -> list[Any]:
    """Add this turn to the pydantic-ai history as a plain prompt/answer pair, so a
    later turn on an API model still sees it."""
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    prior = ModelMessagesTypeAdapter.validate_python(prior_json) if prior_json else []
    turn = [
        ModelRequest(parts=[UserPromptPart(content=message)]),
        ModelResponse(parts=[TextPart(content=answer)], model_name=model_spec),
    ]
    return ModelMessagesTypeAdapter.dump_python([*prior, *turn], mode="json")


async def run_engine_stream(
    state,
    message: str,
    model_spec: str,
    chats: ChatStore | None,
    sid: str | None,
    images: list[dict[str, Any]] | None = None,
    review_depth: str = "low",
    effort: str = "high",
) -> AsyncIterator[dict[str, Any]]:
    from pcb_assistant.agent.agent import build_toolset, engine_effort, system_prompt
    from pcb_assistant.engines import config, dispatch, prompts
    from pcb_assistant.engines.base import EngineError, UsageLimitError
    from pcb_assistant.providers.registry import api_fallback

    project = state.project
    provider, _, model = model_spec.partition(":")

    command, _, rest = message.strip().partition(" ")
    if command == ADD_DIR:  # Claude Code's own /add-dir is interactive-only; do it here
        if sid:
            yield {"type": "session", "chat_id": sid}
        reply = add_dir_reply(chats, sid, project, rest)
        yield {"type": "token", "text": reply}
        if chats and sid:
            chats.record(sid, {"q": message, "a": reply, "route": "subscription"}, chats.messages(sid))
        yield {"type": "done"}
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def put(kind: str, ev: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, ev))

    attached: list[dict[str, str]] = []
    paths = []
    for img in images or []:
        image_id = str(img.get("id", ""))
        path = image_path(project, image_id)
        if path is not None:
            paths.append(path)
            attached.append({"id": image_id, "name": str(img.get("name", ""))})

    es = chats.engine_session(sid) if chats and sid else None
    resume = es.get("id") if es and es.get("provider") == provider and es.get("model") == model else None
    transcript = (chats.detail(sid) or {}).get("transcript", []) if chats and sid else []
    toolset = build_toolset(model_spec, project, state.design, review_depth, lambda e: put("progress", e))
    system = system_prompt(project, state.design, review_depth)

    # Set when this generator is closed early (the user stopped the reply): the
    # CLI runs in a worker thread, which cancelling the coroutine can't stop.
    cancel = threading.Event()
    resumed = resume is not None  # cleared if the resume fails and a fresh session starts

    condensed = (chats.get(sid) or {}).get("condensed") if chats and sid else None

    def ask(payload: dict[str, Any]) -> dict[str, Any] | None:
        """On a bridge thread: show the prompt, wait for the user's answer."""
        permission = payload.get("kind") == "permission"
        tool = str(payload.get("tool", ""))
        if permission and sid and tool in _always_allowed.get(sid, set()):
            return {"allow": True}
        p = prompts.open_prompt(payload)
        put("event", {"type": "prompt", "id": p.id, **payload})
        ans = prompts.wait(p, cancel)
        if permission and sid and ans and ans.get("allow") and ans.get("always"):
            _always_allowed.setdefault(sid, set()).add(tool)
        put("event", {"type": "prompt_done", "id": p.id, "label": prompt_label(payload),
                      "outcome": prompt_outcome(payload, ans)})
        return ans

    # The chat's own run options; only Claude Code takes them.
    interactive = (
        {"ask": ask, "project_dir": project, "add_dirs": chats.added_dirs(sid) if chats and sid else []}
        if config.engine_of(model_spec) == "claude" else {}
    )
    slash = message.lstrip().startswith("/")  # a skill or command: the CLI only sees it at the very start

    def work(session: str | None):
        # a fresh session starts from the chat's summary when it has one
        opening = "" if session else (condense_seed(condensed, transcript) if condensed else seed_transcript(transcript))
        prompt, sys_text = (message, system + "\n\n" + opening) if slash and opening else (opening + message, system)
        return dispatch.run(
            model_spec, sys_text, prompt, toolset=toolset,
            on_event=lambda e: put("event", e), on_tool=lambda e: put("event", e),
            session=session, images=paths, effort=engine_effort(model_spec, effort), cancel=cancel,
            **interactive,
        )

    async def pump() -> None:
        nonlocal resumed
        try:
            try:
                res = await asyncio.to_thread(work, resume)
            except UsageLimitError:
                raise
            except EngineError:
                if resume is None:
                    raise
                put("event", {"type": "notice", "text": "Couldn't resume the earlier subscription "
                              "session — continuing from a summary of this chat."})
                resumed = False
                res = await asyncio.to_thread(work, None)
            await queue.put(("result", res))
        except Exception as e:
            await queue.put(("error", e))
        finally:
            await queue.put(("end", None))

    answer_parts: list[str] = []
    thinking_parts: list[str] = []
    tools: list[dict[str, Any]] = []
    team: dict[str, dict[str, Any]] = {}
    result = None
    failure: Exception | None = None

    if sid:
        yield {"type": "session", "chat_id": sid}
    pumper = asyncio.create_task(pump())
    try:
        while True:
            kind, ev = await queue.get()
            if kind == "end":
                break
            if kind == "error":
                failure = ev
            elif kind == "result":
                result = ev
            elif kind == "progress":
                merge_progress(team.setdefault(ev["reviewer"], {"reviewer": ev["reviewer"]}), ev)
                yield {"type": "review_progress", **ev}
            elif ev["type"] == "token":
                answer_parts.append(ev["text"])
                yield ev
            elif ev["type"] == "thinking":
                thinking_parts.append(ev["text"])
                yield ev
            elif ev["type"] == "tool_call":
                args = ev.get("args")
                tools.append({"kind": "call", "name": ev["name"], "info": ""})
                yield {"type": "tool_call", "name": ev["name"], "args": args if isinstance(args, (dict, str)) else str(args)}
            elif ev["type"] == "tool_result":
                tools.append({"kind": "result", "name": ev["name"], "info": ev["summary"]})
                yield ev
                for frame in follow_up_frames(project, ev["name"], ev["summary"]):
                    yield frame
            elif ev["type"] == "prompt_done":  # the history keeps what was asked and answered
                tools.append({"kind": "call", "name": ev["label"], "info": ""})
                tools.append({"kind": "result", "name": ev["label"], "info": ev["outcome"]})
                yield ev
            else:  # prompt, commands, rate_limit, notice
                yield ev
    except (asyncio.CancelledError, GeneratorExit):  # the user stopped the reply
        record_stopped(chats, sid, message, model_spec, attached, answer_parts, thinking_parts, tools, team)
        raise
    finally:
        pumper.cancel()
        if failure is None and result is None:
            cancel.set()

    def record(extra: dict[str, Any], messages: list[Any]) -> None:
        if chats and sid:
            chats.record(sid, {"q": message, "a": "".join(answer_parts), "images": attached,
                               "thinking": "".join(thinking_parts).strip(), "tools": tools,
                               "team": list(team.values()), **extra}, messages)

    if failure is None and result is not None:
        answer = "".join(answer_parts) or result.text
        if not answer_parts and answer:
            yield {"type": "token", "text": answer}
            answer_parts.append(answer)
        from pcb_assistant.agent import usage_log

        usage = usage_log.record(
            project, "chat", result.usage, route="subscription", model=model_spec,
            effort=engine_effort(model_spec, effort), resumed=resumed,
            tool_calls=sum(1 for t in tools if t["kind"] == "call"), system_chars=len(system),
            utilization=(result.rate_limit or {}).get("utilization"),
        )
        yield {"type": "usage", **usage}
        if chats and sid:
            chats.set_engine_session(sid, {"provider": provider, "model": model, "id": result.session_id})
            record({"route": "subscription", "usage": usage},
                   append_messages(chats.messages(sid), message, answer, model_spec))
        yield {"type": "done"}
        return

    fallback = api_fallback(model_spec) if isinstance(failure, UsageLimitError) else None
    if fallback is not None:
        yield {"type": "notice", "text": f"Subscription usage limit reached — continuing on your API key ({fallback})."}
        from pcb_assistant.agent.stream import run_agent_stream

        async for frame in run_agent_stream(state, message, fallback, sid, images, review_depth, effort):
            if frame["type"] != "session":
                yield frame
        return

    detail = str(failure) if failure else "the subscription CLI returned nothing"
    record({"error": detail}, chats.messages(sid) if chats and sid else [])
    frame: dict[str, Any] = {"type": "error", "detail": detail}
    if isinstance(failure, UsageLimitError):
        frame.update(code="usage_limit", resets_at=failure.resets_at)
    yield frame
