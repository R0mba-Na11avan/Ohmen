"""Claude through the user's Claude Code subscription (the `claude` CLI).

Model ids are the API's own (`claude-sonnet-5`, …) when the API listing is
known, else the CLI's aliases (opus/sonnet/haiku). Datasheet PDFs are read
natively: the engine saves the PDF to its temp dir and the CLI opens it with Read.
"""
from __future__ import annotations

import re

from pcb_assistant.providers.base import Capability
from pcb_assistant.providers.claude import thinks_by_default

PROVIDER = "claude-sub"

CAPABILITIES: dict[str, Capability] = {}

RECOMMENDED = re.compile(r"^(sonnet|claude-sonnet-\d+(-\d)?)$")

_ONE_SHOT_SYSTEM = "You are an expert electronics engineer. Answer exactly in the format the user asks for."


def family(model: str) -> Capability:
    return Capability(
        provider=PROVIDER, model=model, context_tokens=200_000,
        supports_pdf=True, supports_tools=True, supports_vision=True,
        supports_thinking=model in ("opus", "sonnet") or thinks_by_default(model),
        cost_tier="subscription", notes="Runs on your Claude subscription via Claude Code.",
    )


def ask(model: str, prompt: str, pdf: bytes | None) -> str:
    from pcb_assistant.engines import dispatch

    return dispatch.run(f"{PROVIDER}:{model}", _ONE_SHOT_SYSTEM, prompt, pdf=pdf, persist=False).text
