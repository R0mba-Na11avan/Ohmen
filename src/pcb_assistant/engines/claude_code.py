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

Interactive chat (`ask` given) loosens this on purpose, Claude Code-style:
- the cwd is the project folder, plus `--add-dir` for the run's temp files and any
  folder the user added with /add-dir;
- read-only built-ins (Read, Glob, Grep) plus AskUserQuestion, or every built-in
  tool when Settings > Advanced allows it;
- `--permission-prompt-tool` sends each permission check and every
  AskUserQuestion to the app (the MCP bridge), which shows it in the chat and
  blocks until the user answers; `--permission-mode default`, so a user's own
  defaultMode can't skip that;
- optionally the user's own setup (`--setting-sources user`: skills, plugins,
  CLAUDE.md), so their /skill-name commands work; their hooks stay off.

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
from typing import Any, Callable

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
READ_TOOLS = ("Read", "Glob", "Grep", "AskUserQuestion")
PERMISSION_TOOL = f"mcp__{mcp_bridge.SERVER_NAME}__{mcp_bridge.PERMISSION_TOOL}"
NO_HOOKS = json.dumps({"disableAllHooks": True})

# Asks the user something and blocks for the answer (None: stopped, or no answer).
#   {"kind": "permission", "tool", "input"}   -> {"allow": bool, "message"?: str}
#   {"kind": "question", "tool", "questions"} -> {"answers": {question: label}}
Ask = Callable[[dict[str, Any]], "dict[str, Any] | None"]

_INTERACTIVE_NOTE = """

## Running in Ohmen through Claude Code
You are inside the Ohmen desktop app, driven through the user's Claude Code CLI. \
Your working directory is the KiCad project folder. Besides the Ohmen tools you \
have these Claude Code tools: {tools}. When you need the engineer to choose between \
options, use AskUserQuestion: Ohmen shows it as a card they answer in the chat. A \
tool call that needs permission ({gated}) is shown to them as an Allow / Deny card; \
if they deny it, don't retry the same call, ask or move on. To give you another \
folder they type /add-dir <path> in the chat."""


FOOTPRINT_DENIED = (
    "Ohmen doesn't let a footprint file be written by hand: it would skip the spec checks, KiCad's "
    "read-back and the engineer's approval card. Build it with create_footprint (footprint_guide has "
    "the spec format; get_footprint_spec to change one Ohmen made). To look at an existing footprint "
    "file, use Read."
)
_FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_SHELL_TOOLS = {"Bash", "PowerShell"}


def writes_footprint(tool: str, inp: dict[str, Any]) -> bool:
    """Whether a built-in tool call would write a KiCad footprint file. A shell
    command that so much as names one counts: Read is there for looking."""
    if tool in _FILE_TOOLS:
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        return path.lower().endswith(".kicad_mod")
    if tool in _SHELL_TOOLS:
        return ".kicad_mod" in str(inp.get("command") or "").lower()
    return False


def decide(ask: Ask, args: dict[str, Any]) -> dict[str, Any]:
    """Turn one permission-prompt-tool call into the CLI's allow/deny reply,
    asking the user through `ask`."""
    name = str(args.get("tool_name") or "")
    inp = args.get("input") if isinstance(args.get("input"), dict) else {}
    if writes_footprint(name, inp):  # never reaches the user: there's no right answer but the tool
        return {"behavior": "deny", "message": FOOTPRINT_DENIED}
    if name == "AskUserQuestion":
        ans = ask({"kind": "question", "tool": name, "questions": inp.get("questions") or []}) or {}
        answers = ans.get("answers") if isinstance(ans.get("answers"), dict) else None
        if not answers:
            return {"behavior": "deny", "message": str(ans.get("message") or "The user didn't answer.")}
        return {"behavior": "allow",
                "updatedInput": {**inp, "answers": {str(k): str(v) for k, v in answers.items()}}}
    ans = ask({"kind": "permission", "tool": name, "input": inp})
    if ans and ans.get("allow"):
        return {"behavior": "allow", "updatedInput": inp}
    default = "The user declined this." if ans else "The user didn't answer, so this was not allowed."
    return {"behavior": "deny", "message": str((ans or {}).get("message") or default)}


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
    ask: Ask | None = None,
    project_dir: str | Path | None = None,
    add_dirs: list[str] | None = None,
) -> EngineResult:
    """Run one turn to completion. `session` resumes an earlier CLI session;
    setting `cancel` kills the CLI (the user stopped the reply). `ask` makes the
    run interactive (the chat): see the module docstring."""
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
        # allow-listed: reads inside the cwd (the temp dir; or the project with the
        # temp dir added, in the chat) need no permission. Anything else would:
        # -p mode denies it, and the chat asks the user.
        needs_read = pdf is not None or toolset is not None or bool(images)

        interactive = ask is not None and toolset is not None
        prefs = config.settings(NAME)
        cwd = work
        tools = "Read" if needs_read else ""
        if interactive:
            if project_dir is not None and Path(project_dir).is_dir():
                cwd = Path(project_dir)
            tools = "default" if prefs.full_tools else ",".join(READ_TOOLS + (("Skill",) if prefs.user_setup else ()))
            system += _INTERACTIVE_NOTE.format(
                tools="every built-in tool" if prefs.full_tools else ", ".join(tools.split(",")),
                gated="an edit, a shell command, a web fetch, a file outside the project"
                if prefs.full_tools else "a file outside the project",
            )
            (work / "system.md").write_text(system, encoding="utf-8")

        bridge_cm = (
            mcp_bridge.serve(toolset, on_tool, attach_dir=work / "attach", max_calls=max_tool_calls,
                             permission=(lambda a: decide(ask, a)) if interactive else None)
            if toolset is not None else nullcontext(None)
        )
        with bridge_cm as bridge:
            args = [
                path, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--model", model,
                "--system-prompt-file", str(work / "system.md"),
                "--strict-mcp-config",
                "--tools", tools,
            ]
            if interactive:
                args += ["--permission-mode", "default", "--permission-prompt-tool", PERMISSION_TOOL]
                if prefs.user_setup:
                    args += ["--setting-sources", "user", "--settings", NO_HOOKS]
                else:
                    args += ["--setting-sources", "", "--disable-slash-commands"]
                extra = [str(work)] if cwd != work else []
                extra += [d for d in add_dirs or [] if Path(d).is_dir()]
                for d in extra:
                    args += ["--add-dir", d]
            else:
                args += ["--setting-sources", "", "--disable-slash-commands"]
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
            result = _drive(args, prompt, cwd, on_event, timeout, cancel, interactive)
            if bridge is not None and bridge.limited:
                result.rate_limit = {**(result.rate_limit or {}), "step_limited": True}
            return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _drive(
    args: list[str], prompt: str, cwd: Path, on_event: OnEvent | None, timeout: int,
    cancel: threading.Event | None = None, interactive: bool = False,
) -> EngineResult:
    # An MCP call (a long review, or a prompt waiting on the user) can take
    # minutes; the CLI's own default gives up after about one.
    env = {"MCP_TOOL_TIMEOUT": str(timeout * 1000), **_env()}
    proc = subprocess.Popen(
        args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    if cancel is not None:
        kill_on_cancel(proc, cancel)
    parser = StreamParser(on_event, interactive=interactive)
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


def _input_tokens(u: dict[str, Any]) -> int:
    return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


class StreamParser:
    """Folds `claude -p --output-format stream-json` lines into an EngineResult."""

    def __init__(self, on_event: OnEvent | None = None, interactive: bool = False):
        self.on_event = on_event
        # The chat also hears about the CLI's own tool calls (skills, file reads)
        # and its slash commands; other callers only take text frames.
        self.interactive = interactive
        self.builtin_calls: dict[str, str] = {}  # tool_use id -> trace label
        self.session_id: str | None = None
        self.parts: list[str] = []
        self.result: dict[str, Any] | None = None
        self.rate_limit: dict[str, Any] | None = None
        # Per model request (message id -> its usage): partial messages repeat a
        # message once per content block, so the last copy wins.
        self.request_usage: dict[str, dict[str, Any]] = {}

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
            if self.interactive:
                self._emit({"type": "commands",
                            "commands": [str(c) for c in msg.get("slash_commands") or []],
                            "skills": [str(c) for c in msg.get("skills") or []]})
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
        elif kind == "assistant" and msg.get("parent_tool_use_id") is None:
            m = msg.get("message") or {}
            if m.get("id") and m.get("usage"):
                self.request_usage[m["id"]] = m["usage"]
            if self.interactive:
                for block in m.get("content") or []:
                    self._builtin_call(block)
        elif kind == "user" and self.interactive and msg.get("parent_tool_use_id") is None:
            for block in (msg.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    label = self.builtin_calls.pop(str(block.get("tool_use_id")), None)
                    if label is not None:
                        self._emit({"type": "tool_result", "name": label, "summary": _result_text(block)})
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

    def _builtin_call(self, block: Any) -> None:
        """A Claude Code tool call (ours arrive through the bridge instead)."""
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            return
        name, tid = str(block.get("name") or ""), str(block.get("id") or "")
        # ours, a repeat (partial messages repeat a message), or shown as a card
        if not name or name.startswith("mcp__") or tid in self.builtin_calls or name == "AskUserQuestion":
            return
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        label = tool_label(name, inp)
        self.builtin_calls[tid] = label
        self._emit({"type": "tool_call", "name": label, "args": inp})

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
        written = int(u.get("cache_creation_input_tokens") or 0)
        usage = Usage(
            requests=int(r.get("num_turns") or 1),
            tokens_in=int(u.get("input_tokens") or 0) + written + cached,
            tokens_cached=cached,
            tokens_out=int(u.get("output_tokens") or 0),
            tokens_cache_write=written,
            max_context=max((_input_tokens(x) for x in self.request_usage.values()), default=0),
        )
        text = "".join(self.parts).strip() or str(r.get("result") or "")
        return EngineResult(text=text, session_id=self.session_id, usage=usage, rate_limit=self.rate_limit)


def tool_label(name: str, inp: dict[str, Any]) -> str:
    """One trace line for a Claude Code tool: 'Read datasheet.pdf', 'skill tdd', 'Bash: git status'."""
    def short(v: Any, n: int = 60) -> str:
        v = " ".join(str(v or "").split())
        return v if len(v) <= n else v[: n - 1] + "…"

    if name == "Skill":
        return f"skill {short(inp.get('skill') or inp.get('command'))}".strip()
    if name in ("Read", "Edit", "Write", "NotebookEdit") and inp.get("file_path"):
        return f"{name} {Path(str(inp['file_path'])).name}"
    if name in ("Glob", "Grep") and inp.get("pattern"):
        return f"{name} {short(inp['pattern'], 40)}"
    if name in ("Bash", "PowerShell") and inp.get("command"):
        return f"{name}: {short(inp['command'])}"
    if name in ("WebFetch", "WebSearch"):
        return f"{name} {short(inp.get('url') or inp.get('query'))}".strip()
    return name


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    text = " ".join(str(content or "").split())
    return ("error: " + text if block.get("is_error") else text)[:200]
