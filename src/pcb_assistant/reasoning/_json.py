"""Tolerant extraction of JSON from a model response.

Models wrap JSON in prose or ```json fences despite instructions, mention
"[U1]" in a sentence before the real array, and leave trailing commas. This
peels those off and returns the parsed value. Shared by Loop A (candidates),
Loop B (structured findings) and the board understanding.
"""
from __future__ import annotations

import json
import re
from typing import Any

_TRAILING_COMMA = re.compile(r",(\s*[\]}])")


def _candidates(text: str) -> list[str]:
    """Where the JSON might be: each fenced block, then the whole reply."""
    fenced = [m.strip() for m in re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)]
    return [*fenced, text.strip()]


def _extract(text: str, opener: str, kind: type) -> Any:
    decoder = json.JSONDecoder()
    fallback = None
    for chunk in _candidates(text):
        for source in (chunk, _TRAILING_COMMA.sub(r"\1", chunk)):
            # Try every opening bracket in turn: prose like "see [U1]" comes first.
            start = source.find(opener)
            while start != -1:
                try:
                    data, _ = decoder.raw_decode(source, start)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, kind):
                    # Callers want arrays of objects; a citation like "[1]" is a last resort.
                    if kind is not list or not data or isinstance(data[0], dict):
                        return data
                    fallback = data if fallback is None else fallback
                start = source.find(opener, start + 1)
    if fallback is not None:
        return fallback
    name = "array" if kind is list else "object"
    raise json.JSONDecodeError(f"expected a JSON {name}", text, 0)


def extract_json_array(text: str) -> list[Any]:
    """Return the first JSON array found in `text`. Raises json.JSONDecodeError if none."""
    return _extract(text, "[", list)


def extract_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object found in `text`. Raises json.JSONDecodeError if none."""
    return _extract(text, "{", dict)
