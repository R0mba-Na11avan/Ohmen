"""The provider shim — the model-agnostic boundary (parallel to the IR).

One uniform call, `ask(model_spec, prompt, pdf_path)`, dispatched by a
"provider:model" spec to Claude, Gemini, OpenAI, or a local OpenAI-compatible
endpoint (Ollama / LM Studio). Keys come from `credentials` (the desktop app's
Settings, else .env); which models exist comes from live `discovery`. Every model
carries a `Capability` descriptor so the core can warn or gate weak models.

The reasoning core routes ALL model access through here; it never imports an SDK.
"""
from pcb_assistant.providers.base import DEFAULT_MODEL, Capability
from pcb_assistant.providers.registry import ask, available_models, capability, resolve

__all__ = ["DEFAULT_MODEL", "Capability", "ask", "available_models", "capability", "resolve"]
