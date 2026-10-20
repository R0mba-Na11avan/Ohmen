"""Subscription-engine settings, pushed by the desktop app (Settings > Advanced).

Kept apart from API-key credentials: nothing secret lives here — the CLIs hold
their own logins. Env vars are the CLI/dev fallback:
  PCB_ENGINE_CODEX=1, PCB_ENGINE_CLAUDE=1 (enable), PCB_CODEX_PATH, PCB_CLAUDE_PATH.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

ENGINES = ("codex", "claude")

# The API provider each engine stands in for (its "vendor" in the model picker).
VENDOR = {"codex": "openai", "claude": "claude"}
SPEC_PREFIX = {"codex": "codex-sub", "claude": "claude-sub"}

Route = Literal["api", "sub"]


@dataclass(frozen=True)
class EngineSettings:
    enabled: bool = False
    path: str | None = None
    tos_ack: bool = False  # Claude only: the user accepted the ToS warning


@dataclass
class _State:
    engines: dict[str, EngineSettings] = field(default_factory=dict)
    preferred_route: dict[str, Route] = field(default_factory=dict)  # vendor -> route
    fallback_to_api: bool = False
    pushed: bool = False


_state = _State()


def _from_env(engine: str) -> EngineSettings:
    on = os.environ.get(f"PCB_ENGINE_{engine.upper()}", "") in ("1", "true", "yes")
    return EngineSettings(enabled=on, path=os.environ.get(f"PCB_{engine.upper()}_PATH") or None, tos_ack=on)


def settings(engine: str) -> EngineSettings:
    if _state.pushed:
        return _state.engines.get(engine, EngineSettings())
    return _from_env(engine)


def enabled(engine: str) -> bool:
    """Usable at all: switched on, and for Claude, the ToS warning accepted."""
    s = settings(engine)
    return s.enabled and (engine != "claude" or s.tos_ack)


def preferred_route(vendor: str) -> Route:
    return _state.preferred_route.get(vendor, "api")


def fallback_to_api() -> bool:
    return _state.fallback_to_api


def set_all(payload: dict) -> None:
    """Replace every engine setting with what the app pushed."""
    engines = {}
    for name in ENGINES:
        e = (payload.get("engines") or {}).get(name) or {}
        engines[name] = EngineSettings(
            enabled=bool(e.get("enabled")),
            path=(e.get("path") or "").strip() or None,
            tos_ack=bool(e.get("tos_ack")),
        )
    routes = {
        v: r for v, r in (payload.get("preferred_route") or {}).items()
        if v in VENDOR.values() and r in ("api", "sub")
    }
    _state.engines = engines
    _state.preferred_route = routes
    _state.fallback_to_api = bool(payload.get("fallback_to_api"))
    _state.pushed = True


def reset() -> None:
    """Back to env-only (tests)."""
    global _state
    _state = _State()


def engine_of(model_spec: str) -> str | None:
    """'codex-sub:gpt-5' -> 'codex'; None for an API spec."""
    prefix = model_spec.partition(":")[0]
    for name, p in SPEC_PREFIX.items():
        if p == prefix:
            return name
    return None
