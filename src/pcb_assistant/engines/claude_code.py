"""Claude Code engine: `claude -p` driven headless, our tools over MCP.

Isolation, so a user's own Claude Code setup can't leak into a run and the CLI
can't touch their machine:
- `--setting-sources ""` (no user/project settings or hooks), `--disable-slash-commands`,
  `--strict-mcp-config` (only our MCP server), `--system-prompt-file` (our prompt
  replaces Claude Code's), an empty temp dir as cwd.
- `--tools ""`: no built-in tools at all — or just `Read` when a PDF has to be
  read, which only reaches the temp dir.
- ANTHROPIC_API_KEY is stripped from the child env, so the CLI uses the user's
  subscription login rather than silently billing an API key from our .env.

Policy: Anthropic's Consumer Terms restrict Pro/Max credentials to Claude Code
and claude.ai. This engine is off by default and gated behind the ToS
acknowledgement in Settings > Advanced (see engines.config.enabled).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from contextlib import nullcontext
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

NAME = "claude"
MODELS = ("opus", "sonnet", "haiku")  # aliases the CLI resolves to the current model
DEFAULT_TIMEOUT = 1800
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


def cli_path() -> str | None:
    return find_cli(["claude"], config.settings(NAME).path)


def _env() -> dict[str, str]:
    return {k: v for k, v in external_env().items() if k not in _STRIP_ENV}


def detect() -> EngineStatus:
    path = cli_path()
    if path is None:
        return EngineStatus(NAME, installed=False, detail="Claude Code CLI not found. Install it, then run `claude auth login`.")
    status = EngineStatus(NAME, installed=True, path=path)
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20, env=_env())
        status.version = out.stdout.strip().split(" ")[0] or None
        auth = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=30, env=_env())
        info = json.loads(auth.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        status.detail = f"couldn't query the CLI: {e}"
        return status
    status.logged_in = bool(info.get("loggedIn"))
    if not status.logged_in:
        status.detail = "Not logged in — run `claude auth login`."
    elif info.get("authMethod") != "claude.ai":
        status.detail = f"Logged in with {info.get('authMethod')}, not a Claude subscription — usage bills that account."
    else:
        status.detail = f"Claude {info.get('subscriptionType') or ''} subscription".replace("  ", " ")
    return status


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
    persist: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
) -> EngineResult:
    """Run one turn to completion. `session` resumes an earlier CLI session;
    setting `cancel` kills the CLI (the user stopped the reply)."""
    path = cli_path()
    if path is None:
        raise EngineError("Claude Code CLI not found — install it or set its path in Settings › Advanced.")
    work = Path(tempfile.mkdtemp(prefix="pcb-claude-"))
    try:
        if images:  # copied into the cwd, where Read needs no permission
            copies = []
            for i, src in enumerate(images, 1):
                dst = work / f"image-{i}{Path(src).suffix}"
                shutil.copyfile(src, dst)
                copies.append(str(dst))
            prompt = "Attached images (open each with the Read tool): " + ", ".join(copies) + f"\n\n{prompt}"
        if pdf is not None:
            (work / "datasheet.pdf").write_bytes(pdf)
            prompt = (
                f"The datasheet is the PDF at {work / 'datasheet.pdf'} — read it with the Read tool "
                f"before answering.\n\n{prompt}"
            )
        (work / "system.md").write_text(system, encoding="utf-8")
        # Read opens PDFs (ours, or datasheet pages the bridge saves). It's never
        # allow-listed: reads inside the cwd (the temp dir) need no permission,
        # and anything outside it would, which -p mode denies.
        needs_read = pdf is not None or toolset is not None or bool(images)

        bridge_cm = (
            mcp_bridge.serve(toolset, on_tool, attach_dir=work / "attach", max_calls=max_tool_calls)
            if toolset is not None else nullcontext(None)
        )
        with bridge_cm as bridge:
            args = [
                path, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--model", model,
                "--system-prompt-file", str(work / "system.md"),
                "--setting-sources", "",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--tools", "Read" if needs_read else "",
            ]
            if bridge is not None:
                cfg = {"mcpServers": {bridge.name: {"type": "http", "url": bridge.url, "headers": bridge.headers}}}
                (work / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
                args += ["--mcp-config", str(work / "mcp.json"), "--allowedTools", f"mcp__{bridge.name}"]
            if effort:
                args += ["--effort", effort]
            if session:
                args += ["--resume", session]
            elif not persist:
                args.append("--no-session-persistence")
            result = _drive(args, prompt, work, on_event, timeout, cancel)
            if bridge is not None and bridge.limited:
                result.rate_limit = {**(result.rate_limit or {}), "step_limited": True}
            return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _drive(
    args: list[str], prompt: str, cwd: Path, on_event: OnEvent | None, timeout: int,
    cancel: threading.Event | None = None,
) -> EngineResult:
    proc = subprocess.Popen(
        args, cwd=cwd, env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    if cancel is not None:
        kill_on_cancel(proc, cancel)
    parser = StreamParser(on_event)
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            parser.feed(line)
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    return parser.finish(proc.returncode, "".join(stderr))


class StreamParser:
    """Folds `claude -p --output-format stream-json` lines into an EngineResult."""

    def __init__(self, on_event: OnEvent | None = None):
        self.on_event = on_event
        self.session_id: str | None = None
        self.parts: list[str] = []
        self.result: dict[str, Any] | None = None
        self.rate_limit: dict[str, Any] | None = None

    def _emit(self, ev: dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(ev)

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            self.session_id = msg.get("session_id")
        elif kind == "stream_event" and msg.get("parent_tool_use_id") is None:
            ev = msg.get("event") or {}
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    self.parts.append(d["text"])
                    self._emit({"type": "token", "text": d["text"]})
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    self._emit({"type": "thinking", "text": d["thinking"]})
            elif ev.get("type") == "content_block_start" and self.parts and \
                    (ev.get("content_block") or {}).get("type") == "text":
                # A new text block after a tool call: keep the answer readable.
                self.parts.append("\n\n")
                self._emit({"type": "token", "text": "\n\n"})
        elif kind == "rate_limit_event":
            info = msg.get("rate_limit_info") or {}
            window = info.get("rateLimitType") or "five_hour"
            util = ((info.get("unifiedWindows") or {}).get(window) or {}).get("utilization")
            self.rate_limit = {"status": info.get("status"), "window": window,
                               "utilization": util, "resets_at": info.get("resetsAt")}
            self._emit({"type": "rate_limit", **self.rate_limit})
        elif kind == "result":
            self.result = msg
            self.session_id = msg.get("session_id") or self.session_id

    def finish(self, returncode: int | None, stderr: str = "") -> EngineResult:
        r = self.result or {}
        limited = (self.rate_limit or {}).get("status") == "rejected"
        if r.get("is_error") or self.result is None or returncode not in (0, None):
            detail = str(r.get("result") or "").strip() or stderr.strip()[-400:] or f"exit code {returncode}"
            if limited or r.get("api_error_status") == 429 or "usage limit" in detail.lower():
                raise UsageLimitError(f"Claude subscription usage limit reached: {detail}",
                                      resets_at=(self.rate_limit or {}).get("resets_at"))
            raise EngineError(f"Claude Code failed: {detail}")
        u = r.get("usage") or {}
        cached = int(u.get("cache_read_input_tokens") or 0)
        usage = Usage(
            requests=int(r.get("num_turns") or 1),
            tokens_in=int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + cached,
            tokens_cached=cached,
            tokens_out=int(u.get("output_tokens") or 0),
        )
        text = "".join(self.parts).strip() or str(r.get("result") or "")
        return EngineResult(text=text, session_id=self.session_id, usage=usage, rate_limit=self.rate_limit)
