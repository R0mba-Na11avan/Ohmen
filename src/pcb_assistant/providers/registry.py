"""Dispatch a "provider:model" spec to the right provider + capability lookup.

The single entry point the reasoning core uses. Keys come from `credentials`
(Settings-saved, else .env), so callers never touch key handling.

Two kinds of provider:
- API providers (claude, gemini, openai, local): our code calls the vendor API.
- Subscription providers (claude-sub, codex-sub): the user's logged-in CLI runs
  the model (see `engines/`). They stand in for a vendor ("claude", "openai"),
  and the model picker shows each model once with the routes that serve it.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from pcb_assistant.engines import config as engine_config
from pcb_assistant.providers import (
    claude,
    claude_sub,
    codex_sub,
    credentials,
    discovery,
    gemini,
    local,
    openai_compat,
)
from pcb_assistant.providers.base import Capability

_PROVIDERS = {
    "claude": claude,
    "gemini": gemini,
    "openai": openai_compat,   # official OpenAI, BYO key
    "local": local,            # Ollama / LM Studio / any OpenAI-compatible URL
    "claude-sub": claude_sub,  # Claude Code CLI on the user's subscription
    "codex-sub": codex_sub,    # Codex CLI on the user's ChatGPT subscription
}

# Subscription provider -> (engine, the API provider it stands in for).
SUB_PROVIDERS = {"claude-sub": ("claude", "claude"), "codex-sub": ("codex", "openai")}


def _split(model_spec: str) -> tuple[str, str]:
    provider, _, model = model_spec.partition(":")
    if not provider or not model:
        raise ValueError(
            f"model_spec must be 'provider:model', got {model_spec!r}. "
            f"Providers: {', '.join(_PROVIDERS)}."
        )
    if provider not in _PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}. Known: {', '.join(_PROVIDERS)}.")
    return provider, model


def resolve(model_spec: str):
    """Return the provider module for a spec (validates the spec)."""
    provider, _ = _split(model_spec)
    return _PROVIDERS[provider]


def capability(model_spec: str) -> Capability:
    """Capability descriptor for any model a provider serves.

    Layered: the curated entry if we've profiled the model (notes, cost tier),
    else the provider's family rule — then refined by whatever the list-models
    API reported (context size, PDF/image support), since the API's facts beat
    our hardcoded ones. Local models' tools/vision come from the user's Settings
    overrides via `local.family`. Never raises for an unknown model id.
    """
    provider, model = _split(model_spec)
    mod = _PROVIDERS[provider]
    base = mod.CAPABILITIES.get(model) or mod.family(model)
    updates = {k: v for k, v in discovery.meta(provider, model).items() if k != "recommended"}
    return base.model_copy(update=updates) if updates else base


def mark_recommended(caps: list[Capability]) -> list[Capability]:
    """One "recommended" per provider: the newest model matching its RECOMMENDED family."""
    best: dict[str, Capability] = {}
    for c in caps:
        pattern = getattr(_PROVIDERS[c.provider], "RECOMMENDED", None)
        if pattern is None or not pattern.match(c.model):
            continue
        if c.provider not in best or _version(c.model) > _version(best[c.provider].model):
            best[c.provider] = c
    chosen = {c.spec for c in best.values()}
    return [c.model_copy(update={"recommended": c.spec in chosen}) for c in caps]


def _version(model: str) -> tuple[int, ...]:
    """'claude-sonnet-5-5' -> (5, 5); 'gpt-5.1' -> (5, 1). Aliases sort lowest."""
    return tuple(int(n) for n in re.findall(r"\d+", model))


def available_models() -> list[Capability]:
    """Every model the user can pick right now, one entry per route: configured
    providers with a working key, plus enabled subscription engines.
    Recommended first, then curated, then the rest alphabetically — per provider."""
    out: list[Capability] = []
    for provider in credentials.configured():
        listing = discovery.listing(provider)
        if not listing.ok:
            continue
        mod = _PROVIDERS[provider]
        ids = discovery.drop_snapshots(set(listing.models) | set(mod.CAPABILITIES))
        out.extend(capability(f"{provider}:{mid}") for mid in ids)
    for provider, (engine, _vendor) in SUB_PROVIDERS.items():
        if engine_config.enabled(engine):
            out.extend(capability(f"{provider}:{mid}") for mid in sub_models(provider))
    out = mark_recommended(out)
    order = list(_PROVIDERS)
    out.sort(key=lambda c: (
        order.index(c.provider), not c.recommended,
        c.model not in _PROVIDERS[c.provider].CAPABILITIES, c.model,
    ))
    return out


# --- subscription model lists ---

_sub_cache: dict[str, list[str]] = {}
_sub_lock = threading.Lock()


def sub_models(provider: str) -> list[str]:
    """Models a subscription engine serves. Claude Code takes the API's model ids
    (when an API listing is known) or its own aliases; Codex lists its own."""
    with _sub_lock:
        if provider in _sub_cache:
            return _sub_cache[provider]
    if provider == "claude-sub":
        from pcb_assistant.engines.claude_code import MODELS

        api = discovery.listing("claude") if credentials.get("claude") else None
        ids = sorted(discovery.drop_snapshots(set(api.models))) if api and api.ok else list(MODELS)
    else:
        from pcb_assistant.engines import codex

        ids = codex.list_models()
    with _sub_lock:
        _sub_cache[provider] = ids
    return ids


def clear_sub_models() -> None:
    with _sub_lock:
        _sub_cache.clear()


# --- the model picker: one row per model, with the routes that serve it ---

VENDOR_OF = {"claude": "claude", "openai": "openai", "gemini": "gemini", "local": "local",
             "claude-sub": "claude", "codex-sub": "openai"}


@dataclass
class PickerModel:
    cap: Capability                                   # the route the user would get now
    vendor: str
    routes: dict[str, str] = field(default_factory=dict)  # "api"/"sub" -> spec


def picker_models() -> list[PickerModel]:
    """Merge API and subscription entries for the same vendor+model into one row.

    The row's capability comes from the vendor's preferred route when that route
    serves the model, else from the route that does — so a row never silently
    switches billing: its badge shows which route is active.
    """
    rows: dict[tuple[str, str], PickerModel] = {}
    for cap in available_models():
        vendor = VENDOR_OF[cap.provider]
        route = "sub" if cap.provider in SUB_PROVIDERS else "api"
        row = rows.setdefault((vendor, cap.model), PickerModel(cap=cap, vendor=vendor))
        row.routes[route] = cap.spec
        if route == engine_config.preferred_route(vendor):
            row.cap = cap
        row.cap = row.cap.model_copy(update={"recommended": row.cap.recommended or cap.recommended})
    return list(rows.values())


def ask(
    model_spec: str,
    prompt: str,
    pdf_path: str | Path | None = None,
    pdf_bytes: bytes | None = None,
) -> str:
    """Uniform model call. A native PDF may be supplied as a path or as bytes
    (bytes win if both are given — Loop A fetches datasheets straight to bytes).

    A subscription at its usage limit falls back to the same vendor's API key
    only when the user opted in (Settings > Advanced); otherwise it raises.
    """
    from pcb_assistant.engines.base import UsageLimitError

    provider, model = _split(model_spec)
    pdf = pdf_bytes if pdf_bytes is not None else (
        Path(pdf_path).read_bytes() if pdf_path else None
    )
    try:
        return _PROVIDERS[provider].ask(model, prompt, pdf)
    except UsageLimitError:
        fallback = api_fallback(model_spec)
        if fallback is None:
            raise
        return ask(fallback, prompt, pdf_bytes=pdf)


def api_fallback(model_spec: str) -> str | None:
    """The API spec to use when a subscription is spent — only if opted in and keyed."""
    provider, model = _split(model_spec)
    if provider not in SUB_PROVIDERS or not engine_config.fallback_to_api():
        return None
    vendor = SUB_PROVIDERS[provider][1]
    if credentials.get(vendor) is None:
        return None
    if provider == "claude-sub" and model in ("opus", "sonnet", "haiku"):
        api = [c.model for c in available_models() if c.provider == "claude" and model in c.model]
        if not api:
            return None
        model = max(api, key=_version)
    return f"{vendor}:{model}"
