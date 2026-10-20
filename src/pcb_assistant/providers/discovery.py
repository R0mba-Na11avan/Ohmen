"""Live model discovery — ask each configured provider which models it serves.

This is what lets a new model show up in the picker without a code change: the
provider's own list-models API is the source of truth for *which* models exist;
the curated CAPABILITIES tables only add notes / cost / "recommended" on top.

Results are cached per provider (with whatever metadata the API returns — context
size, PDF/image support for Claude) until the credentials change or the user
hits refresh. A listing doubles as a key check: a bad key fails here, loudly.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from pcb_assistant.providers import credentials

_TIMEOUT_S = 10

# Snapshot ids (gpt-4o-2024-08-06, claude-x-20250514) duplicate their alias; hide
# a snapshot when its alias is listed too.
_SNAPSHOT = re.compile(r"^(?P<alias>.+?)-(\d{8}|\d{4}-\d{2}-\d{2})$")

# Non-chat model families the picker should never offer.
_GEMINI_SKIP = ("embedding", "image", "tts", "audio", "live", "aqa", "imagen", "veo",
                "robotics", "computer-use", "transcribe")
_OPENAI_CHAT = re.compile(r"^(gpt-|o\d|chatgpt-)")
_OPENAI_SKIP = ("audio", "realtime", "tts", "transcribe", "search", "image",
                "instruct", "embedding", "moderation")


@dataclass
class Listing:
    ok: bool
    models: dict[str, dict] = field(default_factory=dict)  # id -> capability updates
    error: str = ""


_cache: dict[str, Listing] = {}
_lock = threading.Lock()


def drop_snapshots(ids: set[str]) -> set[str]:
    """Hide dated snapshot ids whose undated alias is also present."""
    return {
        mid for mid in ids
        if not ((m := _SNAPSHOT.match(mid)) and m.group("alias") in ids)
    }


def _list_claude(creds) -> dict[str, dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=creds.api_key, timeout=_TIMEOUT_S)
    out: dict[str, dict] = {}
    for m in client.models.list(limit=100):
        meta: dict = {}
        if m.max_input_tokens:
            meta["context_tokens"] = m.max_input_tokens
        caps = m.capabilities
        if caps is not None:
            meta["supports_pdf"] = caps.pdf_input.supported
            meta["supports_vision"] = caps.image_input.supported
        out[m.id] = meta
    return out


def _list_gemini(creds) -> dict[str, dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=creds.api_key, http_options=types.HttpOptions(timeout=_TIMEOUT_S * 1000)
    )
    out: dict[str, dict] = {}
    for m in client.models.list():
        mid = (m.name or "").removeprefix("models/")
        if not mid.startswith("gemini-") or any(s in mid for s in _GEMINI_SKIP):
            continue
        if "generateContent" not in (m.supported_actions or []):
            continue
        meta: dict = {}
        if m.input_token_limit:
            meta["context_tokens"] = m.input_token_limit
        out[mid] = meta
    return out


def _list_openai(creds) -> dict[str, dict]:
    from openai import OpenAI

    client = OpenAI(api_key=creds.api_key, timeout=_TIMEOUT_S)
    return {
        m.id: {}
        for m in client.models.list()
        if _OPENAI_CHAT.match(m.id) and not any(s in m.id for s in _OPENAI_SKIP)
    }


def _list_local(creds) -> dict[str, dict]:
    from openai import OpenAI

    client = OpenAI(
        api_key=creds.api_key or "not-needed-for-local",
        base_url=creds.base_url,
        timeout=5,  # a local server is either up or it isn't
        max_retries=0,
    )
    return {m.id: {} for m in client.models.list()}


_LISTERS = {
    "claude": _list_claude,
    "gemini": _list_gemini,
    "openai": _list_openai,
    "local": _list_local,
}


def _short(e: Exception) -> str:
    msg = str(e).strip() or type(e).__name__
    return msg if len(msg) <= 240 else msg[:237] + "..."


def refresh(provider: str) -> Listing:
    """List the provider's models now (network), replacing the cache entry."""
    creds = credentials.get(provider)
    if creds is None:
        listing = Listing(ok=False, error="not configured")
    else:
        try:
            listing = Listing(ok=True, models=_LISTERS[provider](creds))
        except Exception as e:  # auth / network / malformed endpoint
            listing = Listing(ok=False, error=_short(e))
    with _lock:
        _cache[provider] = listing
    return listing


def refresh_all() -> dict[str, Listing]:
    """Re-list every provider in parallel (each may take a network round trip)."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(credentials.PROVIDERS)) as pool:
        listings = pool.map(refresh, credentials.PROVIDERS)
    return dict(zip(credentials.PROVIDERS, listings))


def listing(provider: str) -> Listing:
    """Cached listing; lists on first use (e.g. a dev key that came from .env)."""
    with _lock:
        cached = _cache.get(provider)
    if cached is not None:
        return cached
    return refresh(provider)


def meta(provider: str, model: str) -> dict:
    """Capability updates the API reported for a model (empty if unknown/unlisted)."""
    with _lock:
        cached = _cache.get(provider)
    return dict(cached.models.get(model, {})) if cached else {}


def clear() -> None:
    with _lock:
        _cache.clear()
