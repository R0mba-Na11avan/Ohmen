"""Google (Gemini) provider. Datasheet passed as a native PDF part."""
from __future__ import annotations

import re

from pcb_assistant.providers import credentials
from pcb_assistant.providers.base import Capability


def _gemini(model: str, cost_tier, notes: str = "") -> Capability:
    return Capability(
        provider="gemini", model=model, context_tokens=1_000_000,
        supports_pdf=True, supports_tools=True, supports_vision=True,
        supports_thinking=True, cost_tier=cost_tier, notes=notes,
    )


# Plain Flash (no -lite/-preview); the newest one listed is "recommended".
RECOMMENDED = re.compile(r"^gemini-\d+(\.\d+)?-flash$")


# Curated overlay — notes, cost tier and "recommended" for models we've profiled.
# Any other Gemini model the API lists still works via `family()`.
CAPABILITIES: dict[str, Capability] = {
    "gemini-3.1-pro-preview": _gemini(
        "gemini-3.1-pro-preview", "mid",
        "Needs billing enabled (free tier returns limit:0).",
    ),
    "gemini-3.8-flash": _gemini(
        "gemini-3.8-flash", "free",
        "Free tier; passes the floating-EN eval (evals/eval_loop_b.py).",
    ),
    # Alternates for when 3.8 Flash returns 503 "high demand".
    "gemini-3.5-flash": _gemini(
        "gemini-3.5-flash", "free", "Previous Flash; fallback when 3.8 Flash is overloaded.",
    ),
    "gemini-3.5-flash-lite": _gemini(
        "gemini-3.5-flash-lite", "free",
        "Newer Flash-Lite; cheap and fast — fine for chat, weaker on review.",
    ),
    "gemini-3.1-flash-lite": _gemini(
        "gemini-3.1-flash-lite", "free",
        "Lightest/cheapest; least capable — fine for chat, weaker on review.",
    ),
}


def family(model: str) -> Capability:
    """Gemini chat models are multimodal (PDF + images), call tools and think."""
    return _gemini(model, "unknown")


def client():
    import logging

    from google import genai

    # Silence the benign "AFC ... not recommended" notice; we don't use tools here.
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    return genai.Client(api_key=credentials.require("gemini").api_key)


def ask(model: str, prompt: str, pdf: bytes | None) -> str:
    from google.genai import types

    contents: list = []
    if pdf is not None:
        contents.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
    contents.append(prompt)

    resp = client().models.generate_content(model=model, contents=contents)
    return resp.text or ""
