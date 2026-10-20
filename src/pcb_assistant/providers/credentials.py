"""Provider credentials — the one place keys come from.

Two sources, in priority order:
  1. "saved": pushed by the desktop app (keys the user entered in Settings,
     stored encrypted by Electron and handed over at sidecar start / on save).
  2. "env": environment variables, including the repo .env (dev fallback, and
     the CLI's only source).

Providers never read os.environ for keys; they call `get(provider)`. Env is read
lazily on every call so a test's monkeypatch (or a late load_dotenv) is seen.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv

# Dev fallback: the project-root .env (walks up from cwd); no-op if absent.
load_dotenv()

PROVIDERS = ("claude", "gemini", "openai", "local")

# Per-model capability overrides for local models: {model_id: {"tools": bool, "vision": bool}}
Overrides = dict[str, dict[str, bool]]


@dataclass(frozen=True)
class Creds:
    api_key: str | None = field(default=None, repr=False)  # never in logs or tracebacks
    base_url: str | None = None
    source: Literal["saved", "env"] = "env"
    overrides: Overrides = field(default_factory=dict)


_saved: dict[str, Creds] = {}


def _from_env(provider: str) -> Creds | None:
    env = os.environ.get
    if provider == "claude":
        key = env("ANTHROPIC_API_KEY")
        return Creds(api_key=key) if key else None
    if provider == "gemini":
        key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
        return Creds(api_key=key) if key else None
    if provider == "openai":
        key = env("OPENAI_API_KEY")
        return Creds(api_key=key) if key else None
    if provider == "local":
        base = env("LOCAL_BASE_URL")
        return Creds(api_key=env("LOCAL_API_KEY"), base_url=base) if base else None
    return None


def get(provider: str) -> Creds | None:
    """Active credentials for a provider (saved beats env), or None if unconfigured."""
    return _saved.get(provider) or _from_env(provider)


def configured() -> list[str]:
    return [p for p in PROVIDERS if get(p) is not None]


def set_all(payload: dict[str, dict | None]) -> None:
    """Replace every saved credential with what the app pushed.

    `payload` maps provider -> {api_key?, base_url?, overrides?} (or None/absent
    for "nothing saved", which falls back to env). A provider counts as saved
    when it has a key — or, for local, a base URL.
    """
    _saved.clear()
    for provider, entry in payload.items():
        if provider not in PROVIDERS or not entry:
            continue
        key = (entry.get("api_key") or "").strip() or None
        base = (entry.get("base_url") or "").strip().rstrip("/") or None
        if provider == "local" and base is None:
            continue
        if provider != "local" and key is None:
            continue
        _saved[provider] = Creds(
            api_key=key,
            base_url=base if provider == "local" else None,
            source="saved",
            overrides=dict(entry.get("overrides") or {}),
        )


def require(provider: str) -> Creds:
    creds = get(provider)
    if creds is None:
        raise RuntimeError(
            f"no credentials for provider {provider!r} — add a key in Settings "
            "(or set it in .env for development)"
        )
    return creds


# --- non-LLM services (parts distributors) ---
# Kept apart from PROVIDERS: these never list models or answer prompts.

SERVICES = ("digikey",)

_services: dict[str, dict[str, str]] = {}


def _service_from_env(service: str) -> dict[str, str] | None:
    if service == "digikey":
        cid, secret = os.environ.get("DIGIKEY_CLIENT_ID"), os.environ.get("DIGIKEY_CLIENT_SECRET")
        if cid and secret:
            return {"client_id": cid, "client_secret": secret, "source": "env"}
    return None


def service(name: str) -> dict[str, str] | None:
    """Saved (Settings) beats env; None when not configured."""
    return _services.get(name) or _service_from_env(name)


def set_services(payload: dict[str, dict | None]) -> None:
    """Replace every saved service credential with what the app pushed."""
    _services.clear()
    for name, entry in payload.items():
        if name not in SERVICES or not entry:
            continue
        cid = (entry.get("client_id") or "").strip()
        secret = (entry.get("client_secret") or "").strip()
        if cid and secret:
            _services[name] = {"client_id": cid, "client_secret": secret, "source": "saved"}

