"""Per-launch token guard for the loopback sidecar.

The sidecar holds the user's API keys, and any web page in their browser can
reach 127.0.0.1 — a WebSocket isn't stopped by CORS, and a DNS-rebinding page
can even look same-origin. So the desktop app generates a random token per
launch, passes it in PCB_SIDECAR_TOKEN, and every request must carry it
(X-PCB-Token header, or ?token= where a header is impossible: WebSocket, <img>).
Requests must also name a loopback Host, which defeats DNS rebinding.

With no PCB_SIDECAR_TOKEN set (tests, `pcb serve --no-auth`) the guard is off;
plain `pcb serve` generates and prints a token.
"""
from __future__ import annotations

import json
import os
import secrets
from urllib.parse import parse_qs

_EXEMPT_PATHS = {"/healthz"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


def _host_ok(headers: dict[bytes, bytes]) -> bool:
    host = headers.get(b"host", b"").decode("latin-1")
    return host.split(":", 1)[0] in _LOOPBACK_HOSTS


def _presented_token(scope, headers: dict[bytes, bytes]) -> str:
    tok = headers.get(b"x-pcb-token", b"").decode("latin-1")
    if tok:
        return tok
    qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    return (qs.get("token") or [""])[0]


class TokenGuard:
    """Pure ASGI middleware (BaseHTTPMiddleware can't see WebSockets)."""

    def __init__(self, app, token: str | None = None):
        self.app = app
        self.token = token if token is not None else os.environ.get("PCB_SIDECAR_TOKEN", "")

    async def __call__(self, scope, receive, send):
        if not self.token or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        # CORS preflights carry no custom headers; the CORS layer answers them.
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        if scope.get("path") in _EXEMPT_PATHS:
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        if _host_ok(headers) and secrets.compare_digest(
            _presented_token(scope, headers), self.token
        ):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = json.dumps({"detail": "unauthorized"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
