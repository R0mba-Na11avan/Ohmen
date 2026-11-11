"""Questions a subscription CLI puts to the user mid-turn, answered in the app.

Claude Code asks through its permission-prompt tool (served by the MCP bridge):
"may I run this tool?" and, for AskUserQuestion, "which of these?". The bridge
thread that receives the ask blocks here until the user answers in the chat
(POST /chat/prompts/{id}), the turn is stopped, or the wait times out.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WAIT = 1800  # seconds; the CLI run itself times out at the same mark


@dataclass
class Pending:
    id: str
    payload: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    answer: dict[str, Any] | None = None


_pending: dict[str, Pending] = {}
_lock = threading.Lock()


def open_prompt(payload: dict[str, Any]) -> Pending:
    p = Pending(id=secrets.token_urlsafe(9), payload=payload)
    with _lock:
        _pending[p.id] = p
    return p


def wait(p: Pending, cancel: threading.Event | None = None, timeout: float = DEFAULT_WAIT) -> dict[str, Any] | None:
    """Block until the prompt is answered. None if the turn was stopped or the
    user never answered."""
    waited = 0.0
    try:
        while not p.done.wait(0.25):
            waited += 0.25
            if (cancel is not None and cancel.is_set()) or waited >= timeout:
                return None
        return p.answer
    finally:
        with _lock:
            _pending.pop(p.id, None)


def answer(prompt_id: str, value: dict[str, Any]) -> bool:
    """Deliver the user's answer. False if no turn is waiting on that prompt."""
    with _lock:
        p = _pending.get(prompt_id)
    if p is None or p.done.is_set():
        return False
    p.answer = value
    p.done.set()
    return True


def is_pending(prompt_id: str) -> bool:
    with _lock:
        return prompt_id in _pending
