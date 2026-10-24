"""Drive the agent and yield JSON-able frames for the WebSocket layer."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

from pcb_assistant.agent.agent import (
    BOM_MUTATING_TOOLS,
    DIAGRAM_TOOLS,
    FOOTPRINT_TOOLS,
    REVIEW_TOOLS,
    build_agent,
    system_prompt,
)
from pcb_assistant.agent.chat_images import image_path, media_type_of
from pcb_assistant.agent.chats import ChatStore
from pcb_assistant.bom.store import BomStore


MAX_STEPS_KEPT = 120
LIFECYCLE = {"planning", "started", "done", "failed"}


def merge_progress(row: dict[str, Any], ev: dict[str, Any]) -> None:
    """Fold one review_progress event into a reviewer's transcript row.

    Lifecycle events (planning/started/done/failed) set the row's status; step,
    finding and usage events add to it without changing the status. The UI's
    applyProgress (ReviewTeam.tsx) folds the same way.
    """
    status = ev.get("status")
    if status in LIFECYCLE:
        if status in ("started", "planning"):
            row.update(steps=[], last=None)
        row.update({k: v for k, v in ev.items() if v is not None})
    elif status == "step":
        row.setdefault("steps", []).append(ev.get("summary", ""))
        del row["steps"][:-MAX_STEPS_KEPT]
        row["last"] = ev.get("summary")
    elif status == "finding":
        row["findings"] = ev.get("findings", row.get("findings", 0))
        row.setdefault("steps", []).append(f"⚑ {ev.get('summary', '')}")
        row["last"] = ev.get("summary")
    elif status == "usage":
        for k in ("requests", "tokens_in", "tokens_cached", "tokens_cache_write", "tokens_out"):
            if ev.get(k) is not None:
                row[k] = ev[k]


def _starts_turn(message: dict[str, Any]) -> bool:
    return message.get("kind") == "request" and any(
        p.get("part_kind") == "user-prompt" for p in message.get("parts", [])
    )


# Turns at the end of the history kept verbatim when older ones are shrunk.
RECENT_TURNS_KEPT = 2
# Tool results up to this size stay as they are: they cost little, and short
# answers ("Added row ...") carry the conversation.
TOOL_RESULT_KEPT_CHARS = 600


def _shrink_part(part: dict[str, Any]) -> dict[str, Any]:
    kind = part.get("part_kind")
    if kind == "tool-return":
        content = part.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        if len(text) <= TOOL_RESULT_KEPT_CHARS:
            return part
        head = " ".join(text[:200].split())
        stub = (f"[earlier {part.get('tool_name', 'tool')} result, {len(text):,} chars, "
                f"trimmed to save context; call the tool again if you need it] {head}…")
        return {**part, "content": stub}
    if kind == "user-prompt" and isinstance(part.get("content"), list):
        # Images already seen: a note instead of the bytes.
        content = [
            f"[image {c.get('identifier', '')} attached earlier]"
            if isinstance(c, dict) and c.get("kind") == "binary" else c
            for c in part["content"]
        ]
        return {**part, "content": content}
    return part


def shrink_old_turns(
    messages: list[dict[str, Any]], keep_turns: int = RECENT_TURNS_KEPT
) -> list[dict[str, Any]]:
    """Old tool results (datasheet pages, netlist dumps) cut to a short stub and
    old images to a note; the newest `keep_turns` turns and all the words of
    the conversation stay. Those results are most of a long chat's size, and
    the agent can call the tool again."""
    starts = [i for i, m in enumerate(messages) if _starts_turn(m)]
    if len(starts) <= keep_turns:
        return messages
    cut = starts[-keep_turns]
    old = [{**m, "parts": [_shrink_part(p) for p in m.get("parts", [])]} for m in messages[:cut]]
    return old + messages[cut:]


def fit_history(messages: list[dict[str, Any]], context_tokens: int) -> list[dict[str, Any]]:
    """The stored history, made to fit the model's window.

    Every turn resends the history, so a long chat would otherwise outgrow the
    context and fail on every message after. Budget: about half the window at
    ~3 characters a token, leaving room for the system prompt, the tool list and
    this turn's own tool results. Over budget, old tool results shrink first;
    if that isn't enough, the oldest whole turns go. Cuts only where a user turn
    starts, so a tool call is never separated from its result.
    """
    budget = context_tokens * 3 // 2
    if sum(len(json.dumps(m)) for m in messages) <= budget:
        return messages
    messages = shrink_old_turns(messages)
    sizes = [len(json.dumps(m)) for m in messages]
    remaining = sum(sizes)
    if remaining <= budget:
        return messages
    starts = [i for i, m in enumerate(messages) if _starts_turn(m)]
    for i in range(len(messages)):
        if i in starts and i > 0 and remaining <= budget:
            return messages[i:]
        remaining -= sizes[i]
    return messages[starts[-1]:] if starts else []


def _context_tokens(model_spec: str) -> int:
    from pcb_assistant.providers import capability

    try:
        return capability(model_spec).context_tokens
    except Exception:
        return 100_000


def bom_frame(project) -> dict[str, Any]:
    rows = [r.model_dump() for r in BomStore.for_project(project).list()]
    return {"type": "bom", "rows": rows}


def findings_frame(project) -> dict[str, Any]:
    from pcb_assistant.findings.current import load_review

    rev = load_review(project)
    fs = rev.get("findings", [])
    counts = {
        "must_fix": sum(1 for f in fs if f.get("severity") == "must-fix"),
        "verify": sum(1 for f in fs if f.get("severity") == "verify"),
        "cosmetic": sum(1 for f in fs if f.get("severity") == "cosmetic"),
        "muted": rev.get("muted", 0),
    }
    return {"type": "findings", "counts": counts}


def diagram_frame(project) -> dict[str, Any]:
    from pcb_assistant.reasoning.diagram_cache import load_diagram

    d = load_diagram(project)
    return {"type": "diagram", "mermaid": d.get("mermaid", ""), "note": d.get("note", "")}


FOOTPRINT_PROPOSAL = re.compile(r"Footprint proposal \[(\w+)\]")


def follow_up_frames(project, name: str, summary: str) -> list[dict[str, Any]]:
    """What changed after a tool ran: the BOM, the findings receipt, the diagram."""
    out = []
    if name in BOM_MUTATING_TOOLS:
        out.append(bom_frame(project))
    if name in REVIEW_TOOLS and project is not None:
        out.append(findings_frame(project))
    # Only on success — a failed redraw leaves the last diagram as-is.
    if name in DIAGRAM_TOOLS and project is not None and summary.startswith("Diagram updated"):
        out.append(diagram_frame(project))
    if name in FOOTPRINT_TOOLS and (m := FOOTPRINT_PROPOSAL.match(summary)):
        out.append({"type": "footprint", "id": m.group(1)})
    return out


def record_stopped(chats, sid, message, model_spec, attached, answer_parts, thinking_parts, tools, team) -> None:
    """Keep a stopped turn: what arrived shows on reload, and the history gets the
    partial answer so a later "carry on" has something to carry on from."""
    if not (chats and sid):
        return
    from pcb_assistant.agent.engine_stream import append_messages

    answer = "".join(answer_parts)
    chats.record(
        sid,
        {"q": message, "a": answer, "thinking": "".join(thinking_parts).strip(), "tools": tools,
         "team": list(team.values()), "images": attached, "stopped": True},
        append_messages(chats.messages(sid), message, answer, model_spec) if answer else chats.messages(sid),
    )


async def run_agent_stream(
    state,
    message: str,
    model_spec: str,
    chat_id: str | None = None,
    images: list[dict[str, Any]] | None = None,
    review_depth: str = "low",
    effort: str = "high",
    condense: tuple[str, int | None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield frames: session / condensed / thinking / token / tool_call /
    tool_result / review_progress / bom / findings / diagram / done / error.

    `state` is the sidecar AppState. Conversation history lives in the per-project
    ChatStore keyed by `chat_id` (a new session is created if none is given); the
    BOM/memory it touches stay project-global. `images` are `{id, name}` refs to
    files already uploaded to the chat image store; they ride along with `message`.
    """
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        ModelMessagesTypeAdapter,
        PartDeltaEvent,
        PartStartEvent,
        TextPart,
        TextPartDelta,
        ThinkingPart,
        ThinkingPartDelta,
    )

    project = state.project
    chats = ChatStore.for_project(project) if project else None
    sid = chats.ensure(chat_id) if chats else None

    from pcb_assistant.engines.config import engine_of

    if chats and sid and condense:  # a long or long-idle chat starts this turn from a summary
        from pcb_assistant.agent import condense as condensing

        limit = condensing.threshold(condense[0], condense[1], _context_tokens(model_spec))
        session = chats.get(sid) or {}
        reason = condensing.due(session, limit)
        if reason:
            yield {"type": "session", "chat_id": sid}
            yield {"type": "condensing"}
            system_chars = len(system_prompt(project, state.design, review_depth))
            event = await condensing.condense(chats, sid, model_spec, limit, reason, system_chars)
            if event:
                yield {"type": "condensed", **event}

    if engine_of(model_spec) is not None:  # a subscription CLI runs this turn
        from pcb_assistant.agent.engine_stream import run_engine_stream

        async for frame in run_engine_stream(
            state, message, model_spec, chats, sid, images, review_depth, effort
        ):
            yield frame
        return

    prior = fit_history(chats.messages(sid), _context_tokens(model_spec)) if chats else []
    history = ModelMessagesTypeAdapter.validate_python(prior) if prior else None

    # Agent events and review-team progress share one queue: reviewers report from
    # the tool's worker thread while the agent stream is parked on that tool call,
    # so progress has to reach the client by a path that isn't the agent stream.
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    team: dict[str, dict[str, Any]] = {}  # reviewer -> latest row, kept for the transcript

    def on_progress(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("progress", event))

    agent = build_agent(model_spec, project, state.design, review_depth, on_progress, effort=effort)

    # Images go ahead of the text; refs that don't resolve are dropped.
    attached: list[dict[str, str]] = []
    image_parts: list[Any] = []
    for img in images or []:
        image_id = str(img.get("id", ""))
        path = image_path(project, image_id)
        if path is None:
            continue
        image_parts.append(BinaryContent(data=path.read_bytes(), media_type=media_type_of(image_id)))
        attached.append({"id": image_id, "name": str(img.get("name", ""))})
    prompt: Any = [*image_parts, *([message] if message else [])] if image_parts else message

    answer_parts: list[str] = []
    thinking_parts: list[str] = []
    tools: list[dict[str, Any]] = []
    final_messages = None
    run_result = None

    try:
        if sid:  # tell the client which session this landed in (esp. a fresh one)
            yield {"type": "session", "chat_id": sid}

        async def pump() -> None:
            try:
                async with agent.run_stream_events(prompt, message_history=history) as stream:
                    async for ev in stream:
                        await queue.put(("event", ev))
            except Exception as e:  # surfaced by the consumer below
                await queue.put(("error", e))
            finally:
                await queue.put(("end", None))

        pumper = asyncio.create_task(pump())
        try:
            while True:
                kind, ev = await queue.get()
                if kind == "end":
                    break
                if kind == "error":
                    raise ev
                if kind == "progress":
                    merge_progress(team.setdefault(ev["reviewer"], {"reviewer": ev["reviewer"]}), ev)
                    yield {"type": "review_progress", **ev}
                    continue
                # A part's first chunk can arrive on its start event, the rest as deltas.
                if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                    if ev.part.content:
                        answer_parts.append(ev.part.content)
                        yield {"type": "token", "text": ev.part.content}
                elif isinstance(ev, PartStartEvent) and isinstance(ev.part, ThinkingPart):
                    # Separate thinking blocks (e.g. between tool calls) by a blank line.
                    text = ("\n\n" if thinking_parts else "") + (ev.part.content or "")
                    if text:
                        thinking_parts.append(text)
                        yield {"type": "thinking", "text": text}
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, ThinkingPartDelta):
                    if ev.delta.content_delta:
                        thinking_parts.append(ev.delta.content_delta)
                        yield {"type": "thinking", "text": ev.delta.content_delta}
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                    if ev.delta.content_delta:
                        answer_parts.append(ev.delta.content_delta)
                        yield {"type": "token", "text": ev.delta.content_delta}
                elif isinstance(ev, FunctionToolCallEvent):
                    args = ev.part.args
                    tools.append(
                        {"kind": "call", "name": ev.part.tool_name,
                         "info": args if isinstance(args, str) else ""}
                    )
                    yield {
                        "type": "tool_call",
                        "name": ev.part.tool_name,
                        "args": args if isinstance(args, (dict, str)) else str(args),
                    }
                elif isinstance(ev, FunctionToolResultEvent):
                    name = ev.part.tool_name
                    summary = str(ev.part.content)[:500]
                    tools.append({"kind": "result", "name": name, "info": summary})
                    yield {"type": "tool_result", "name": name, "summary": summary}
                    for frame in follow_up_frames(project, name, summary):
                        yield frame
                elif type(ev).__name__ == "AgentRunResultEvent":
                    final_messages = ev.result.all_messages()
                    run_result = ev.result
        finally:
            pumper.cancel()

        usage = None
        if run_result is not None:
            from pydantic_ai.messages import ModelResponse

            from pcb_assistant.agent import usage_log

            responses = [m for m in run_result.new_messages() if isinstance(m, ModelResponse)]
            usage = usage_log.record(
                project, "chat", usage_log.from_run_usage(run_result.usage(), responses), route="api",
                model=model_spec, effort=effort, tool_calls=sum(1 for t in tools if t["kind"] == "call"),
                history_chars=len(json.dumps(prior)) if prior else 0,
            )
            yield {"type": "usage", **usage}
        if chats and sid:
            msgs_json = (
                ModelMessagesTypeAdapter.dump_python(final_messages, mode="json")
                if final_messages is not None
                else chats.messages(sid)
            )
            chats.record(
                sid,
                {"q": message, "a": "".join(answer_parts), "thinking": "".join(thinking_parts).strip(),
                 "tools": tools, "team": list(team.values()), "images": attached, "usage": usage},
                msgs_json,
            )
        yield {"type": "done"}
    except (asyncio.CancelledError, GeneratorExit):  # the user stopped the reply
        record_stopped(chats, sid, message, model_spec, attached, answer_parts, thinking_parts, tools, team)
        raise
    except Exception as e:  # provider/tool failure mid-stream
        if chats and sid:
            chats.record(
                sid,
                {"q": message, "a": "".join(answer_parts), "thinking": "".join(thinking_parts).strip(),
                 "tools": tools, "team": list(team.values()), "images": attached, "error": str(e)},
                chats.messages(sid),
            )
        yield {"type": "error", "detail": str(e)}
