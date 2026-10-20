"""Codex engine: `codex app-server` (JSON-RPC over stdio), our tools over MCP.

One app-server process per run. It gets our MCP server through `-c` config
overrides (with its tools pre-approved), runs in an empty temp dir with a
read-only sandbox, and has every optional feature off — shell, web search,
plugins, apps, browser, the user's own MCP servers. Any approval request other
than a call to our MCP server is declined. The user's ChatGPT login (from
`codex login`, or the Codex desktop app) is used as-is; we never see the token.

Verified live against codex-cli 0.162 (the Codex desktop app's bundled CLI).
Feature names move between releases; an unknown one is ignored, so a rename
re-enables that feature rather than breaking the run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import nullcontext
from itertools import count
from pathlib import Path
from typing import Any

from pcb_assistant.engines import config, mcp_bridge
from pcb_assistant.engines.base import (
    EngineError,
    EngineResult,
    EngineStatus,
    OnEvent,
    Usage,
    UsageLimitError,
    find_cli,
    kill_on_cancel,
)
from pcb_assistant.procenv import external_env

NAME = "codex"
DEFAULT_TIMEOUT = 1800
_STRIP_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")  # use the ChatGPT login, not our API key

# The Codex desktop app ships its own CLI without putting `codex` on PATH.
_APP_BUNDLED = [
    Path.home() / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe",  # Windows (Store app)
    Path("/Applications/Codex.app/Contents/Resources/codex"),  # macOS
]

# Everything Codex can do besides call our tools. A run needs none of it, and
# each one either touches the user's machine or bloats every request's prompt.
_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "remote_plugin", "browser_use",
    "browser_use_external", "in_app_browser", "computer_use", "image_generation",
    "multi_agent", "hooks", "skill_search", "skill_mcp_dependency_install", "tool_suggest",
    "goals", "sleep_tool", "view_image", "memories", "workspace_dependencies",
)  # not code_mode_host: current Codex calls MCP tools through it


def _user_mcp_servers() -> list[str]:
    """MCP servers in the user's ~/.codex/config.toml. `-c mcp_servers=...` merges
    with them rather than replacing them, so each is switched off by name."""
    import tomllib

    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        with open(home / "config.toml", "rb") as f:
            return list((tomllib.load(f).get("mcp_servers") or {}).keys())
    except (OSError, tomllib.TOMLDecodeError):
        return []


def cli_path() -> str | None:
    found = find_cli(["codex"], config.settings(NAME).path)
    if found or config.settings(NAME).path:
        return found
    return next((str(p) for p in _APP_BUNDLED if p.is_file()), None)


def _env() -> dict[str, str]:
    return {k: v for k, v in external_env().items() if k not in _STRIP_ENV}


def _toml_str(s: str) -> str:
    return json.dumps(s)  # a JSON string is a valid TOML basic string


class AppServer:
    """A JSON-RPC session with one `codex app-server` process."""

    def __init__(self, path: str, cwd: Path, overrides: list[str], on_message=None, trusted_server: str | None = None):
        args = [path, "app-server"]
        for o in overrides:
            args += ["-c", o]
        self.trusted_server = trusted_server  # our MCP server: its tool-call approvals are accepted
        self.proc = subprocess.Popen(
            args, cwd=cwd, env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        )
        self.stderr: list[str] = []
        threading.Thread(target=lambda: self.stderr.extend(self.proc.stderr), daemon=True).start()
        self._ids = count(1)
        self.on_message = on_message  # notifications + server requests

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def messages(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def request(self, method: str, params: dict) -> Any:
        """Send a request; dispatch everything else until its response arrives."""
        msg_id = next(self._ids)
        self.send({"id": msg_id, "method": method, "params": params})
        for msg in self.messages():
            if msg.get("id") == msg_id and "method" not in msg:
                if "error" in msg:
                    raise EngineError(f"codex {method}: {(msg['error'] or {}).get('message', msg['error'])}")
                return msg.get("result")
            self.dispatch(msg)
        raise EngineError(f"codex app-server exited during {method}: {self.stderr_tail()}")

    def dispatch(self, msg: dict) -> None:
        if "method" in msg and "id" in msg:  # a request from the server: approvals etc.
            self.send({"id": msg["id"], "result": self.answer(msg["method"], msg.get("params") or {})})
        if self.on_message:
            self.on_message(msg)

    def answer(self, method: str, params: dict) -> dict:
        """Refuse everything except a tool call to our own MCP server."""
        if method == "mcpServer/elicitation/request":
            ours = self.trusted_server is not None and params.get("serverName") == self.trusted_server
            return {"action": "accept", "content": {}} if ours else {"action": "decline"}
        if method in ("applyPatchApproval", "execCommandApproval"):
            return {"decision": "denied"}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}}
        return {"decision": "decline"}  # command execution / file change approvals

    def stderr_tail(self) -> str:
        return "".join(self.stderr).strip()[-400:]

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        if self.proc.poll() is None:
            self.proc.kill()


def _start(path: str, cwd: Path, overrides: list[str], on_message=None, trusted_server: str | None = None) -> AppServer:
    server = AppServer(path, cwd, overrides, on_message, trusted_server)
    server.request("initialize", {"clientInfo": {"name": "ohmen", "title": "Ohmen", "version": "0.1"}})
    server.send({"method": "initialized"})
    return server


def detect() -> EngineStatus:
    path = cli_path()
    if path is None:
        return EngineStatus(NAME, installed=False, detail="Codex CLI not found. Install it (npm i -g @openai/codex), then run `codex login`.")
    status = EngineStatus(NAME, installed=True, path=path)
    work = Path(tempfile.mkdtemp(prefix="pcb-codex-"))
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20, env=_env())
        status.version = (out.stdout.strip().split() or [None])[-1]
        server = _start(path, work, [])
        try:
            info = server.request("account/read", {}) or {}
        finally:
            server.close()
    except (OSError, subprocess.SubprocessError, EngineError) as e:
        status.detail = f"couldn't query the CLI: {e}"
        return status
    finally:
        shutil.rmtree(work, ignore_errors=True)
    account = info.get("account")
    status.logged_in = bool(account)
    if not account:
        status.detail = "Not logged in — run `codex login`."
    elif account.get("type") != "chatgpt":
        status.detail = "Logged in with an API key, not a ChatGPT subscription — usage bills that key."
    else:
        status.detail = f"ChatGPT {account.get('planType') or ''} plan".replace("  ", " ")
    return status


def list_models() -> list[str]:
    path = cli_path()
    if path is None:
        return []
    work = Path(tempfile.mkdtemp(prefix="pcb-codex-"))
    try:
        server = _start(path, work, [])
        try:
            res = server.request("model/list", {}) or {}
        finally:
            server.close()
    except (OSError, subprocess.SubprocessError, EngineError):
        return []
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return [m.get("model") or m.get("id") for m in res.get("data", []) if m.get("model") or m.get("id")]


def run(
    model: str,
    system: str,
    prompt: str,
    *,
    toolset=None,
    on_event: OnEvent | None = None,
    on_tool: mcp_bridge.ToolEvent | None = None,
    session: str | None = None,
    pdf: bytes | None = None,
    images: list[Path] | None = None,
    max_tool_calls: int | None = None,
    effort: str | None = None,
    persist: bool = True,  # app-server threads always persist; kept for a common signature
    timeout: int = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
) -> EngineResult:
    """Run one turn to completion. `session` is a thread id to resume; setting
    `cancel` kills the app-server (the user stopped the reply)."""
    if pdf is not None:
        from pcb_assistant.providers.pdf_text import inline_pdf

        prompt = inline_pdf(prompt, pdf)
    path = cli_path()
    if path is None:
        raise EngineError("Codex CLI not found — install it or set its path in Settings › Advanced.")
    work = Path(tempfile.mkdtemp(prefix="pcb-codex-"))
    bridge_cm = (
        mcp_bridge.serve(toolset, on_tool, attach_dir=None, max_calls=max_tool_calls)
        if toolset is not None else nullcontext(None)
    )
    try:
        with bridge_cm as bridge:
            overrides = run_overrides(bridge)
            parser = TurnParser(on_event)
            server = _start(path, work, overrides, parser.feed, bridge.name if bridge else None)
            timer = threading.Timer(timeout, server.proc.kill)
            timer.start()
            if cancel is not None:
                kill_on_cancel(server.proc, cancel)
            try:
                common = {"model": model, "cwd": str(work), "approvalPolicy": "never", "sandbox": "read-only"}
                if session:
                    res = server.request("thread/resume", {"threadId": session, **common})
                else:
                    res = server.request("thread/start", {
                        **common, "developerInstructions": system, "ephemeral": not persist,
                    })
                parser.thread_id = ((res or {}).get("thread") or {}).get("id") or session
                inputs: list[dict[str, Any]] = [{"type": "localImage", "path": str(p)} for p in images or []]
                inputs.append({"type": "text", "text": prompt})
                params: dict[str, Any] = {"threadId": parser.thread_id, "input": inputs}
                if effort:
                    params["effort"] = effort
                try:
                    server.request("turn/start", params)
                except EngineError:
                    if effort != "xhigh":
                        raise
                    params["effort"] = "high"  # this model tops out below xhigh
                    server.request("turn/start", params)
                for msg in server.messages():
                    server.dispatch(msg)
                    if parser.done:
                        break
            finally:
                timer.cancel()
                server.close()
            return parser.finish(server.stderr_tail())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_overrides(bridge: mcp_bridge.Bridge | None) -> list[str]:
    """`-c` overrides for one run: read-only sandbox, no approvals, no web search,
    every optional feature off, and our MCP server as the only one (replacing the
    user's own, whose tools would otherwise ride along on every request)."""
    out = [
        'sandbox_mode="read-only"',
        'approval_policy="never"',
        'web_search="disabled"',
        *(f"features.{f}=false" for f in _DISABLED_FEATURES),
        *(f"mcp_servers.{_toml_key(name)}.enabled=false" for name in _user_mcp_servers()
          if bridge is None or name != bridge.name),
    ]
    if bridge is not None:
        auth = _toml_str("Bearer " + bridge.token)
        out += [
            f"mcp_servers.{bridge.name}.url={_toml_str(bridge.url)}",
            f"mcp_servers.{bridge.name}.http_headers={{Authorization={auth}}}",
            f'mcp_servers.{bridge.name}.default_tools_approval_mode="approve"',
        ]
    return out


def _toml_key(name: str) -> str:
    return name if name.replace("_", "").replace("-", "").isalnum() else _toml_str(name)


class TurnParser:
    """Folds app-server notifications for one turn into an EngineResult."""

    def __init__(self, on_event: OnEvent | None = None):
        self.on_event = on_event
        self.thread_id: str | None = None
        self.parts: list[str] = []
        self.final: list[str] = []  # completed agentMessage items, if deltas never came
        self.phases: dict[str, str | None] = {}  # agentMessage item id -> "commentary" | "final_answer"
        self.usage = Usage()
        self.rate_limit: dict[str, Any] | None = None
        self.error: str | None = None
        self.error_info: Any = None
        self.done = False

    def _emit(self, ev: dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(ev)

    def feed(self, msg: dict) -> None:
        method, p = msg.get("method"), msg.get("params") or {}
        if method == "item/agentMessage/delta" and p.get("delta"):
            # "commentary" messages narrate between tool calls ("I'll check the
            # datasheet…"): shown as thinking, kept out of the answer.
            if self.phases.get(p.get("itemId")) == "commentary":
                self._emit({"type": "thinking", "text": p["delta"]})
            else:
                self.parts.append(p["delta"])
                self._emit({"type": "token", "text": p["delta"]})
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta") and p.get("delta"):
            self._emit({"type": "thinking", "text": p["delta"]})
        elif method == "item/started" and (p.get("item") or {}).get("type") == "agentMessage":
            item = p["item"]
            self.phases[item.get("id")] = item.get("phase")
            if item.get("phase") == "commentary":
                self._emit({"type": "thinking", "text": "\n\n"})
            elif self.parts:
                self.parts.append("\n\n")
                self._emit({"type": "token", "text": "\n\n"})
        elif method == "item/completed" and (p.get("item") or {}).get("type") == "agentMessage":
            if p["item"].get("phase") != "commentary":
                self.final.append((p["item"].get("text") or ""))
        elif method == "thread/tokenUsage/updated":
            total = ((p.get("tokenUsage") or {}).get("total")) or {}
            self.usage = Usage(
                requests=self.usage.requests + 1,
                tokens_in=int(total.get("inputTokens") or 0),
                tokens_cached=int(total.get("cachedInputTokens") or 0),
                tokens_out=int(total.get("outputTokens") or 0),
            )
        elif method == "account/rateLimits/updated":
            primary = ((p.get("rateLimits") or {}).get("primary")) or {}
            used = primary.get("usedPercent")
            self.rate_limit = {
                "status": "allowed", "window": f"{primary.get('windowDurationMins') or 300}m",
                "utilization": (used / 100) if isinstance(used, (int, float)) else None,
                "resets_at": primary.get("resetsAt"),
            }
            self._emit({"type": "rate_limit", **self.rate_limit})
        elif method == "error":
            err = p.get("error") or {}
            self.error = err.get("message") or str(err)
            self.error_info = err.get("codexErrorInfo")
            if not p.get("willRetry"):
                self.done = True
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            if turn.get("status") == "failed":
                err = turn.get("error") or {}
                self.error = err.get("message") or self.error or "turn failed"
                self.error_info = err.get("codexErrorInfo") or self.error_info
            self.done = True

    def finish(self, stderr: str = "") -> EngineResult:
        if self.error or not self.done:
            detail = self.error or stderr or "the app-server stopped before the turn finished"
            info = json.dumps(self.error_info) if self.error_info else ""
            if "usageLimitExceeded" in info or "usage limit" in detail.lower():
                raise UsageLimitError(f"Codex usage limit reached: {detail}",
                                      resets_at=(self.rate_limit or {}).get("resets_at"))
            raise EngineError(f"Codex failed: {detail}")
        text = "".join(self.parts).strip() or "\n\n".join(self.final).strip()
        return EngineResult(text=text, session_id=self.thread_id, usage=self.usage, rate_limit=self.rate_limit)
