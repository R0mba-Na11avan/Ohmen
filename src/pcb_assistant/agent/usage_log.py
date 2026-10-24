"""Token usage per chat turn and per reviewer run, for finding where it goes.

Each turn (and each reviewer) appends one line to
`<project>/.pcb_assistant/usage.jsonl` and the chat gets a `usage` frame, so the
split between fresh input, cache writes, cache reads and output is visible
rather than folded into one "tokens in" number.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from pcb_assistant.engines.base import Usage

log = logging.getLogger(__name__)

FILE = "usage.jsonl"


def usage_fields(usage: Usage) -> dict[str, int]:
    """The usage split the UI and the log show: fresh input is what's neither
    read from nor written to the prompt cache."""
    return {
        "requests": usage.requests,
        "tokens_fresh": max(0, usage.tokens_in - usage.tokens_cached - usage.tokens_cache_write),
        "tokens_cache_write": usage.tokens_cache_write,
        "tokens_cached": usage.tokens_cached,
        "tokens_out": usage.tokens_out,
        "max_context": usage.max_context,
    }


def from_run_usage(run_usage: Any, responses: list[Any] = ()) -> Usage:
    """A pydantic-ai RunUsage (plus the run's ModelResponses, for the largest
    single request) as an engine Usage."""
    largest = 0
    for r in responses:
        u = getattr(r, "usage", None)
        if u is not None:
            largest = max(largest, u.input_tokens + u.cache_read_tokens + u.cache_write_tokens)
    return Usage(
        requests=run_usage.requests,
        tokens_in=run_usage.input_tokens + run_usage.cache_read_tokens + run_usage.cache_write_tokens,
        tokens_cached=run_usage.cache_read_tokens,
        tokens_out=run_usage.output_tokens,
        tokens_cache_write=run_usage.cache_write_tokens,
        max_context=largest,
    )


def record(project: str | Path | None, kind: str, usage: Usage, **extra: Any) -> dict[str, Any]:
    """Append one entry to the project's usage log; returns the logged fields."""
    entry = {"ts": round(time.time(), 1), "kind": kind, **usage_fields(usage),
             **{k: v for k, v in extra.items() if v is not None}}
    if project:
        try:
            path = Path(project) / ".pcb_assistant" / FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:  # the log is a diagnostic; never fail a turn over it
            log.warning("couldn't write the usage log: %s", e)
    return entry
