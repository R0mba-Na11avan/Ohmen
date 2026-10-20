"""Local / OpenAI-compatible endpoint provider.

Covers Ollama (http://localhost:11434/v1), LM Studio (http://localhost:1234/v1)
and hosted OpenAI-compatible services (OpenRouter, Groq) — anything with a base
URL that speaks the OpenAI chat API. The key is optional (local runtimes ignore it).

Model ids are whatever the endpoint serves, so nothing is curated: every model
starts conservative (no PDF, no tools, no vision) and the user opts individual
models into tools / vision in Settings, which arrive as credential overrides.
"""
from __future__ import annotations

from pcb_assistant.providers import credentials
from pcb_assistant.providers.base import Capability
from pcb_assistant.providers.openai_compat import chat_ask

CAPABILITIES: dict[str, Capability] = {}


def family(model: str) -> Capability:
    creds = credentials.get("local")
    flags = (creds.overrides.get(model) if creds else None) or {}
    tools = bool(flags.get("tools"))
    vision = bool(flags.get("vision"))
    return Capability(
        provider="local", model=model, context_tokens=8_000,
        supports_pdf=False, supports_tools=tools, supports_vision=vision,
        notes=(
            "Local model; tool-calling enabled in Settings — reliability depends "
            "on the model + runtime."
            if tools
            else "Local model; no native PDF. Enable tool-calling in Settings to use the chat agent."
        ),
    )


def client():
    from openai import OpenAI

    creds = credentials.require("local")
    # Local runtimes ignore the key, but the SDK insists on one.
    return OpenAI(api_key=creds.api_key or "not-needed-for-local", base_url=creds.base_url)


def ask(model: str, prompt: str, pdf: bytes | None) -> str:
    return chat_ask(client(), model, prompt, pdf)
