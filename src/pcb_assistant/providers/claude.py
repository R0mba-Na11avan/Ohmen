"""Anthropic (Claude) provider. Datasheet passed as a native base64 PDF block."""
from __future__ import annotations

import base64
import re

from pcb_assistant.providers import credentials
from pcb_assistant.providers.base import DEFAULT_MAX_TOKENS, Capability

# Curated overlay — notes, cost tier and "recommended" for models we've profiled.
# Any other model the API lists still works via `family()`.
CAPABILITIES: dict[str, Capability] = {
    "claude-opus-5": Capability(
        provider="claude", model="claude-opus-5", context_tokens=200_000,
        supports_pdf=True, supports_tools=True, supports_vision=True,
        supports_thinking=True, cost_tier="high",
        notes="Strongest reasoning; adaptive thinking by default.",
    ),
    "claude-sonnet-5": Capability(
        provider="claude", model="claude-sonnet-5", context_tokens=200_000,
        supports_pdf=True, supports_tools=True, supports_vision=True,
        supports_thinking=True, cost_tier="mid",
        notes="Spike-2 workhorse; passed the review pass bar.",
    ),
    "claude-haiku-4-5": Capability(
        provider="claude", model="claude-haiku-4-5", context_tokens=200_000,
        supports_pdf=True, supports_tools=True, supports_vision=True, cost_tier="low",
    ),
}

# Sonnet is the price/quality sweet spot for review; the newest one listed is "recommended".
RECOMMENDED = re.compile(r"^claude-sonnet-\d+(-\d)?$")

# Opus/Sonnet/Fable 5+ think (adaptively) by default — asking them for a summary
# only changes what's shown, not what's billed. Haiku 4.5 and Opus 4.x don't think
# unless enabled, and enabling it would cost tokens purely for display.
_THINKS_BY_DEFAULT = re.compile(r"^claude-(opus|sonnet|fable)-(\d+)")


def thinks_by_default(model: str) -> bool:
    m = _THINKS_BY_DEFAULT.match(model)
    return bool(m) and int(m.group(2)) >= 5


def family(model: str) -> Capability:
    """Every current Claude model reads PDFs and images and calls tools."""
    return Capability(
        provider="claude", model=model, context_tokens=200_000,
        supports_pdf=True, supports_tools=True, supports_vision=True,
        supports_thinking=thinks_by_default(model),
    )


def client():
    import anthropic

    return anthropic.Anthropic(api_key=credentials.require("claude").api_key)


def ask(model: str, prompt: str, pdf: bytes | None) -> str:
    content: list[dict] = []
    if pdf is not None:
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode(),
            },
        })
    content.append({"type": "text", "text": prompt})

    msg = client().messages.create(
        model=model,
        max_tokens=DEFAULT_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")
