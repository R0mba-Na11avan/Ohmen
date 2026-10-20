"""OpenAI models through the user's ChatGPT subscription (the `codex` CLI).

Codex takes no PDF input, so datasheets go in as extracted page text — the
same fallback the OpenAI API path uses.
"""
from __future__ import annotations

import re

from pcb_assistant.providers.base import Capability
from pcb_assistant.providers.openai_compat import TEXT_ONLY_NOTE

PROVIDER = "codex-sub"

CAPABILITIES: dict[str, Capability] = {}

RECOMMENDED = re.compile(r"^gpt-\d+(\.\d+)?$")

_ONE_SHOT_SYSTEM = "You are an expert electronics engineer. Answer exactly in the format the user asks for."


def family(model: str) -> Capability:
    return Capability(
        provider=PROVIDER, model=model, context_tokens=256_000,
        supports_pdf=False, supports_tools=True, supports_vision=True, supports_thinking=True,
        cost_tier="subscription",
        notes=f"Runs on your ChatGPT subscription via Codex. {TEXT_ONLY_NOTE}",
    )


def ask(model: str, prompt: str, pdf: bytes | None) -> str:
    from pcb_assistant.engines import dispatch

    return dispatch.run(f"{PROVIDER}:{model}", _ONE_SHOT_SYSTEM, prompt, pdf=pdf, persist=False).text
