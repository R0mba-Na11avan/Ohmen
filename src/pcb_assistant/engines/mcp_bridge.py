"""A minimal local MCP server that hands a pydantic-ai toolset to a CLI engine.

The subscription CLIs (Codex, Claude Code) run the model loop themselves; they
reach our tools over MCP's streamable-HTTP transport. Only the JSON-RPC subset
they use is implemented: initialize, ping, tools/list, tools/call — answered as
plain JSON (no SSE stream, which the spec allows).

One stdlib HTTP server per process, bound to 127.0.0.1 on a random port and
started on first use. Each run registers its toolset under a random id and gets
its own bearer token, so another local process can't call a run's tools, and a
finished run's tools are gone.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)

SERVER_NAME = "pcb"  # tools show up in the CLI as mcp__pcb__<tool> / pcb.<tool>
PROTOCOL_VERSION = "2025-06-18"
SUMMARY_CHARS = 500

# on_event receives {"type": "tool_call", "name", "args"} before a tool runs and
# {"type": "tool_result", "name", "summary"} after — the same shape stream.py emits.
ToolEvent = Callable[[dict[str, Any]], None]


@dataclass
class _Run:
    toolset: Any
    token: str
    on_event: ToolEvent | None
    attach_dir: Path | None  # where PDF tool attachments are saved (None: text only)
    max_calls: int | None = None
    calls: int = 0
    files: list[Path] = field(default_factory=list)


@dataclass
class Bridge:
    """Connection details for one run, ready to put in a CLI's MCP config."""

    url: str
    token: str
    _run: _Run
    name: str = SERVER_NAME

    @property
    def calls(self) -> int:
        return self._run.calls

    @property
    def limited(self) -> bool:
        """The run hit `max_calls` and was told to stop."""
        return self._run.max_calls is not None and self._run.calls > self._run.max_calls

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


_runs: dict[str, _Run] = {}
_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None


@contextmanager
def serve(
    toolset,
    on_event: ToolEvent | None = None,
    attach_dir: str | Path | None = None,
    max_calls: int | None = None,
) -> Iterator[Bridge]:
    """Expose `toolset` for the duration of the block. Past `max_calls` tool calls,
    every call answers "stop" — the CLIs have no request cap of their own."""
    port = _ensure_server()
    run_id = secrets.token_urlsafe(12)
    run = _Run(toolset, secrets.token_urlsafe(32), on_event, Path(attach_dir) if attach_dir else None, max_calls)
    with _lock:
        _runs[run_id] = run
    try:
        yield Bridge(url=f"http://127.0.0.1:{port}/mcp/{run_id}", token=run.token, _run=run)
    finally:
        with _lock:
            _runs.pop(run_id, None)


def _ensure_server() -> int:
    global _server
    with _lock:
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            _server.daemon_threads = True
            threading.Thread(target=_server.serve_forever, name="mcp-bridge", daemon=True).start()
        return _server.server_address[1]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the sidecar's stdout clean
        log.debug("mcp-bridge: " + fmt, *args)

    def _run(self) -> _Run | None:
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) != 2 or parts[0] != "mcp":
            return None
        with _lock:
            run = _runs.get(parts[1])
        auth = self.headers.get("Authorization", "")
        if run is None or not hmac.compare_digest(auth, f"Bearer {run.token}"):
            return None
        return run

    def _send(self, status: int, body: Any = None) -> None:
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # no server-initiated stream
        self._send(405)

    def do_DELETE(self):  # session end
        self._send(200 if self._run() else 404)

    def do_POST(self):
        # Read the body before any reply: answering mid-upload makes Windows abort the connection.
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        run = self._run()
        if run is None:
            self._send(404)
            return
        try:
            msg = json.loads(body or b"null")
        except ValueError:
            self._send(400, _error(None, -32700, "parse error"))
            return
        if isinstance(msg, list):
            replies = [r for r in (handle(run, m) for m in msg) if r is not None]
            self._send(200, replies) if replies else self._send(202)
            return
        reply = handle(run, msg)
        self._send(200, reply) if reply is not None else self._send(202)


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(run: _Run, msg: Any) -> dict | None:
    """Answer one JSON-RPC message (None for notifications)."""
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request")
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if msg_id is None:  # notification (e.g. notifications/initialized)
        return None
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ohmen", "version": "1"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [
            {"name": t.name, "description": t.description or "", "inputSchema": t.function_schema.json_schema}
            for t in run.toolset.tools.values()
        ]}
    elif method == "tools/call":
        result = call_tool(run, str(params.get("name", "")), params.get("arguments") or {})
    else:
        return _error(msg_id, -32601, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def call_tool(run: _Run, name: str, args: dict) -> dict:
    tool = run.toolset.tools.get(name)
    if tool is None:
        return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
    run.calls += 1
    if run.max_calls is not None and run.calls > run.max_calls:
        return {"content": [{"type": "text", "text": "Step limit reached — stop calling tools and finish now."}], "isError": True}
    if run.on_event:
        run.on_event({"type": "tool_call", "name": name, "args": args})
    try:
        kwargs = tool.function_schema.validator.validate_python(args)
        content = to_content(run, tool.function(**kwargs))
        is_error = False
    except Exception as e:  # bad arguments or a failing tool: the model sees it and can retry
        content, is_error = [{"type": "text", "text": f"{type(e).__name__}: {e}"}], True
    if run.on_event:
        text = " ".join(c.get("text", "") for c in content if c["type"] == "text")
        run.on_event({"type": "tool_result", "name": name, "summary": text[:SUMMARY_CHARS]})
    return {"content": content, "isError": is_error}


def to_content(run: _Run, value: Any) -> list[dict]:
    """A tool's return value as MCP content blocks.

    Images pass through. A PDF can't ride in an MCP result, so it's saved where
    the CLI's Read tool may open it (Claude Code), or replaced by a note.
    """
    from pydantic_ai import BinaryContent, ToolReturn

    if isinstance(value, ToolReturn):
        items = [value.return_value, *(value.content if isinstance(value.content, list) else [value.content])]
    else:
        items = [value]
    out: list[dict] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, BinaryContent):
            if item.media_type.startswith("image/"):
                out.append({"type": "image", "data": base64.b64encode(item.data).decode(), "mimeType": item.media_type})
            elif item.media_type == "application/pdf" and run.attach_dir is not None:
                run.attach_dir.mkdir(parents=True, exist_ok=True)
                path = run.attach_dir / f"pages-{len(run.files) + 1}.pdf"
                path.write_bytes(item.data)
                run.files.append(path)
                out.append({"type": "text", "text": f"The pages are saved as a PDF at {path} — open it with the Read tool."})
            else:
                out.append({"type": "text", "text": "(This model can't take that attachment; read the pages as text instead.)"})
        else:
            out.append({"type": "text", "text": item if isinstance(item, str) else json.dumps(item, default=str)})
    return out or [{"type": "text", "text": ""}]
