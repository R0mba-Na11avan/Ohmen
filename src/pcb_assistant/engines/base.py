"""Shared types for subscription engines (a vendor CLI driving the model loop).

An engine runs one prompt to completion through the user's installed, logged-in
CLI. The CLI owns auth; we never see the subscription token. Our tools reach it
through the MCP bridge, which reports tool calls itself, so an engine only
reports what the CLI's own stream says: text, thinking, session id, usage and
rate-limit state.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

# Engine stream events, already in stream.py's frame shape:
#   {"type": "token" | "thinking", "text"}  — answer / reasoning deltas
#   {"type": "rate_limit", "utilization": 0..1 | None, "resets_at": epoch | None, "window": str}
OnEvent = Callable[[dict[str, Any]], None]


def kill_on_cancel(proc: subprocess.Popen, cancel: threading.Event) -> None:
    """Kill `proc` as soon as `cancel` is set (the user stopped the reply); the
    watcher ends with the process either way."""

    def watch() -> None:
        while proc.poll() is None:
            if cancel.wait(0.2):
                if proc.poll() is None:
                    proc.kill()
                return

    threading.Thread(target=watch, daemon=True).start()


class EngineError(RuntimeError):
    """The CLI failed (missing, logged out, crashed, or the model errored)."""


class UsageLimitError(EngineError):
    """The subscription's usage window is spent. `resets_at` is epoch seconds when known."""

    def __init__(self, message: str, resets_at: int | None = None):
        super().__init__(message)
        self.resets_at = resets_at


@dataclass
class Usage:
    requests: int = 0
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0

    def add(self, other: "Usage") -> None:
        self.requests += other.requests
        self.tokens_in += other.tokens_in
        self.tokens_cached += other.tokens_cached
        self.tokens_out += other.tokens_out


@dataclass
class EngineResult:
    text: str
    session_id: str | None = None
    usage: Usage = field(default_factory=Usage)
    rate_limit: dict[str, Any] | None = None


@dataclass
class EngineStatus:
    engine: str
    installed: bool
    path: str | None = None
    version: str | None = None
    logged_in: bool | None = None  # None = couldn't tell
    detail: str = ""


def find_cli(names: list[str], override: str | None) -> str | None:
    """A user-pinned path wins; else the first name found on PATH."""
    if override:
        return override if os.path.isfile(override) else None
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def summarize_text(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
