"""Condensing a long chat: older turns become a model-written summary, the
latest few stay word for word.

Every request resends the whole conversation, and every tool call is another
request, so a long chat costs more per message the longer it runs. Past a
threshold (Settings > General) the next message starts from the summary plus
the recent turns instead. Old thinking, tool results and page images go; the
agent can call a tool again, and project notes carry decisions regardless.

Triggers, checked before each message:
- size: the previous request carried more than the threshold;
- idle: over an hour since the last message (the prompt cache has expired, so
  the next message would re-cache everything anyway) and the chat is not tiny;
- manual: the chat menu's "Condense now".

The summary is incremental: a later condense hands the summarizer the previous
summary plus the turns that have aged out since, never the whole chat. If the
summarizer fails, a plain transcript of those turns stands in.

Both routes end up the same: the stored pydantic-ai messages become a summary
exchange plus the recent turns, and a subscription CLI session is dropped so
the next message starts a fresh one seeded with the same (engine_stream).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

LEVELS: dict[str, int] = {"normal": 40_000, "aggressive": 20_000}
CUSTOM_RANGE = (10_000, 150_000)
RECENT_TURNS = 4
IDLE_SECONDS = 3600
IDLE_FLOOR = 15_000  # an idle chat smaller than this isn't worth a summary call
CHARS_PER_TOKEN = 3.5
ANSWER_CHARS = 4000  # per answer handed to the summarizer, and per recent turn seeded
SUMMARY_HEAD = "[Summary of the earlier conversation, condensed to save context]"
SUMMARY_ACK = "Understood. I'll carry on from that summary and the turns that follow."
FALLBACK_CHARS = 8000
TIMEOUT = {"local": 180.0}
DEFAULT_TIMEOUT = 90.0


def threshold(setting: str | None, custom: int | None, context_tokens: int) -> int | None:
    """Tokens a request may carry before the chat is condensed; None when off.
    Never more than half the model's window (a local model's 8k, say)."""
    if setting == "custom" and custom:
        base = max(CUSTOM_RANGE[0], min(CUSTOM_RANGE[1], int(custom)))
    elif setting in LEVELS:
        base = LEVELS[setting]
    else:
        return None
    return min(base, context_tokens // 2)


def _tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def last_request_tokens(session: dict[str, Any]) -> int:
    """The previous request's size: as the engine reported it, else estimated
    from what the chat would resend."""
    for turn in reversed(session.get("transcript", [])):
        usage = turn.get("usage") or {}
        if usage.get("max_context"):
            return int(usage["max_context"])
        if not turn.get("error"):
            break
    return _tokens(len(json.dumps(session.get("messages", []))))


def _recent_start(transcript: list[dict[str, Any]], budget_tokens: int) -> int:
    """Index of the first turn kept word for word: up to RECENT_TURNS that fit in
    the budget, and always the latest one."""
    start = len(transcript)
    used = 0
    for i in range(len(transcript) - 1, -1, -1):
        size = _tokens(len(_turn_text(transcript[i], ANSWER_CHARS)))
        if start < len(transcript) and (len(transcript) - i > RECENT_TURNS or used + size > budget_tokens):
            break
        used += size
        start = i
    return start


def due(session: dict[str, Any], limit: int | None, now: float | None = None) -> str | None:
    """Why this chat should be condensed before the next message, if it should."""
    if limit is None or not session.get("transcript"):
        return None
    size = last_request_tokens(session)
    if size >= limit:
        return "size"
    idle = (now or time.time()) - float(session.get("updated_at") or 0)
    if idle > IDLE_SECONDS and size >= min(IDLE_FLOOR, limit):
        return "idle"
    return None


def _turn_text(turn: dict[str, Any], answer_chars: int) -> str:
    q = (turn.get("q") or "").strip() or "(images only)"
    a = (turn.get("a") or "").strip()
    if len(a) > answer_chars:
        a = a[:answer_chars] + " […]"
    tools = [t for t in turn.get("tools", []) if t.get("kind") == "result"]
    used = "".join(f"\n  - {t['name']}: {' '.join(str(t.get('info', '')).split())[:200]}" for t in tools)
    return f"User: {q}\n" + (f"Tools used:{used}\n" if used else "") + f"Assistant: {a or '(no answer)'}"


_PROMPT = """You are condensing the earlier part of a conversation between an electronics
engineer and their PCB design copilot (KiCad schematic review, BOM, datasheets,
footprints), so the copilot can continue the conversation from your summary alone.

Write a compact summary under these headings, skipping any that are empty:
- Goal: what the engineer is working on.
- Decisions: parts chosen (MPNs, values, packages), design choices, and why.
- Facts established: datasheet values and limits found, with part and page where given.
- Changes made: BOM rows, footprints, notes, anything the copilot changed.
- Open questions and next steps.
- Preferences: how the engineer wants the copilot to work.

Keep every MPN, value, net name, reference designator and page number exactly
as written. No preamble, no closing remarks; at most about 500 words.
"""


def summary_prompt(previous: str, turns: list[dict[str, Any]]) -> str:
    body = "\n\n".join(_turn_text(t, ANSWER_CHARS) for t in turns)
    prior = f"The summary so far, to update and extend:\n\n{previous}\n\n" if previous else ""
    return f"{_PROMPT}\n{prior}The turns to fold in:\n\n{body}\n"


def fallback_summary(previous: str, turns: list[dict[str, Any]]) -> str:
    """Without a summarizer: the earlier summary and the aged turns, cut short."""
    text = "\n\n".join(_turn_text(t, 600) for t in turns)
    out = (previous + "\n\n" if previous else "") + text
    return out if len(out) <= FALLBACK_CHARS else "[…]\n" + out[-FALLBACK_CHARS:]


def summarizer_for(model_spec: str) -> str:
    """The cheapest model Ohmen knows on the chat's own route (same login and
    billing), else the chat model itself."""
    provider, _, model = model_spec.partition(":")
    try:
        if provider == "claude-sub":
            from pcb_assistant.providers.registry import sub_models

            small = [m for m in sub_models(provider) if "haiku" in m]
            return f"claude-sub:{max(small)}" if small else model_spec
        if provider in ("claude", "gemini", "openai"):
            from pcb_assistant.providers.registry import _version, available_models

            cheap = [c for c in available_models() if c.provider == provider and c.cost_tier == "low"]
            if cheap:
                # Gemini lists Flash and Flash-Lite as low; the -lite one is cheaper
                # (by name part: every "gemini" contains "mini")
                def smallest(c):
                    return bool({"lite", "mini", "nano"} & set(c.model.split("-"))), _version(c.model)

                best = max(cheap, key=smallest)
                return best.spec
    except Exception:
        pass
    return model_spec  # local models and Codex: nothing smaller to hand it to


async def summarize(model_spec: str, previous: str, turns: list[dict[str, Any]]) -> tuple[str, str | None]:
    """(summary, the model that wrote it — None when the fallback stood in)."""
    from pcb_assistant.providers.registry import ask

    spec = summarizer_for(model_spec)
    timeout = TIMEOUT.get(spec.partition(":")[0], DEFAULT_TIMEOUT)
    try:
        text = await asyncio.wait_for(asyncio.to_thread(ask, spec, summary_prompt(previous, turns)), timeout)
    except Exception:
        return fallback_summary(previous, turns), None
    text = (text or "").strip()
    return (text, spec) if text else (fallback_summary(previous, turns), None)


def _is_summary(message: dict[str, Any]) -> bool:
    return any(str(p.get("content", "")).startswith(SUMMARY_HEAD) for p in message.get("parts", []))


def condensed_messages(messages: list[dict[str, Any]], summary: str, keep_turns: int,
                       model_spec: str) -> list[dict[str, Any]]:
    """The pydantic-ai history as a summary exchange plus the last `keep_turns`
    turns, cut where a user turn starts so no tool call loses its result."""
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    from pcb_assistant.agent.stream import _starts_turn

    body = [m for m in messages if not _is_summary(m)]
    starts = [i for i, m in enumerate(body) if _starts_turn(m)]
    kept = body[starts[-min(keep_turns, len(starts))]:] if starts and keep_turns else []
    pair = ModelMessagesTypeAdapter.dump_python([
        ModelRequest(parts=[UserPromptPart(content=f"{SUMMARY_HEAD}\n\n{summary}")]),
        ModelResponse(parts=[TextPart(content=SUMMARY_ACK)], model_name=model_spec),
    ], mode="json")
    return [*pair, *kept]


def seed(condensed: dict[str, Any], transcript: list[dict[str, Any]]) -> str:
    """A fresh CLI session's opening: the summary, then the turns since it."""
    recent = "\n\n".join(_turn_text(t, ANSWER_CHARS) for t in transcript[condensed["through"]:]
                         if not (t.get("error") and not t.get("a")))
    return (f"{SUMMARY_HEAD}\n\n{condensed['summary']}\n\n"
            + (f"The most recent turns, word for word:\n\n{recent}\n\n" if recent else "")
            + "---\n\n")


async def condense(chats, sid: str, model_spec: str, limit: int | None, reason: str,
                   system_chars: int = 0) -> dict[str, Any] | None:
    """Condense the chat now. Returns the event (also stored with the chat), or
    None when there's nothing old enough to fold in."""
    session = chats.get(sid)
    if not session:
        return None
    transcript = session.get("transcript", [])
    prior = session.get("condensed") or {}
    budget = max(1000, int((limit or LEVELS["normal"]) * 0.4))
    start = _recent_start(transcript, budget)
    through = int(prior.get("through", 0))
    if start <= through:
        return None  # every turn is either summarized already or recent
    before = last_request_tokens(session)
    summary, by = await summarize(model_spec, prior.get("summary", ""), transcript[through:start])
    kept = len(transcript) - start
    messages = condensed_messages(chats.messages(sid), summary, kept, model_spec)
    after = _tokens(system_chars + len(json.dumps(messages)))
    event = {
        "at_turn": len(transcript), "reason": reason, "summary": summary, "summarizer": by,
        "before": before, "after": after, "through": start, "at": time.time(),
    }
    chats.set_condensed(sid, event, messages)
    return event
