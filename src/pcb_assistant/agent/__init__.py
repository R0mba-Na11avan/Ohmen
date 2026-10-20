"""The chat agent (pydantic-ai) — the conversational, tool-using copilot.

Lives only on the chat path. The one-shot review/parts/diagram paths keep using
the provider shim (`providers.registry`). Tools call the same Phase 1 functions,
so the verification floor holds: board-critical claims come through review/pattern
tools (cited) and datasheet-grounded research, never from free prose.
"""
