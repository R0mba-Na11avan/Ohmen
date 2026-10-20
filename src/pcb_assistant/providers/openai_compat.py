"""OpenAI provider (official API, BYO key) + the chat helper the local provider shares.

The OpenAI-compatible wire format is also what local runtimes (Ollama, LM Studio)
and aggregators (OpenRouter, Groq) speak — those live in `local.py`, which reuses
`chat_ask` here with its own base URL.
"""
from __future__ import annotations

import re

from pcb_assistant.providers import credentials
from pcb_assistant.providers.base import Capability

TEXT_ONLY_NOTE = "Text-only datasheets: reads extracted page text, not the PDF itself."

CAPABILITIES: dict[str, Capability] = {
    "gpt-5": Capability(
        provider="openai", model="gpt-5", context_tokens=256_000,
        supports_pdf=False, supports_tools=True, supports_vision=True,
        cost_tier="high", notes=TEXT_ONLY_NOTE,
    ),
}

# The main GPT line (no -mini/-nano/-pro); the newest one listed is "recommended".
RECOMMENDED = re.compile(r"^gpt-\d+(\.\d+)?$")

# Current chat families that call tools and read images. Anything else the API
# lists (older or niche models) gets the conservative default.
_CAPABLE = re.compile(r"^(gpt-5|gpt-4\.1|gpt-4o|o3|o4)")


def family(model: str) -> Capability:
    capable = bool(_CAPABLE.match(model))
    return Capability(
        provider="openai", model=model,
        context_tokens=128_000 if capable else 8_000,
        supports_pdf=False, supports_tools=capable, supports_vision=capable,
        notes=TEXT_ONLY_NOTE,
    )


def client():
    from openai import OpenAI

    return OpenAI(api_key=credentials.require("openai").api_key)


def chat_ask(openai_client, model: str, prompt: str, pdf: bytes | None) -> str:
    if pdf is not None:  # no native PDF on this wire format: send the page text
        from pcb_assistant.providers.pdf_text import inline_pdf

        prompt = inline_pdf(prompt, pdf)
    resp = openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


def ask(model: str, prompt: str, pdf: bytes | None) -> str:
    return chat_ask(client(), model, prompt, pdf)
