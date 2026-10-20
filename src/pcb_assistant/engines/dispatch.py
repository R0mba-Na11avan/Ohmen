"""Run a `*-sub` model spec on its engine. The one entry point callers use."""
from __future__ import annotations

from pcb_assistant.engines import claude_code, codex, config
from pcb_assistant.engines.base import EngineError, EngineResult

_MODULES = {"claude": claude_code, "codex": codex}
_LABEL = {"claude": "Claude Code", "codex": "Codex"}


def module(engine: str):
    return _MODULES[engine]


def run(model_spec: str, system: str, prompt: str, **kwargs) -> EngineResult:
    """kwargs pass through to the engine's run(): toolset, on_event, on_tool,
    session, pdf, images, max_tool_calls, effort, persist, timeout, cancel."""
    engine = config.engine_of(model_spec)
    if engine is None:
        raise ValueError(f"{model_spec!r} is not a subscription model")
    if not config.enabled(engine):
        raise EngineError(f"The {_LABEL[engine]} subscription is turned off — enable it in Settings › Advanced.")
    return _MODULES[engine].run(model_spec.partition(":")[2], system, prompt, **kwargs)
