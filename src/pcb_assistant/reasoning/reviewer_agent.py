"""An agentic reviewer: one engineer-like agent per block of the board.

Instead of reading a netlist dump once and answering, the reviewer works the way
an engineer does: looks up each part's pins and what they connect to, pulls the
part's datasheet and reads the pinout / application pages, does the arithmetic
(divider voltages, dissipation), and only then reports — through
`report_finding`, which is the evidence gate:

  - every netlist claim ({ref, pin, net}) is checked against the Design; a wrong
    one is refused with the real connection, so the reviewer corrects itself;
  - a datasheet citation must name a page this reviewer actually opened, and its
    quote must appear on that page.

Progress (each tool step, each finding) is reported through `progress` so the
UI can show the reviewer working.
"""
from __future__ import annotations

import ast
import logging
import operator
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from pcb_assistant.analysis import circuit as cm
from pcb_assistant.analysis.values import fmt, parse_value
from pcb_assistant.datasheets.text import normalize
from pcb_assistant.datasheets.tools import DatasheetTools
from pcb_assistant.findings.models import Finding, Severity
from pcb_assistant.ir.models import Design

log = logging.getLogger(__name__)

Progress = Callable[[dict[str, Any]], None]
REQUEST_LIMIT = 24  # per part audit; a fresh, small context for every part
REPLY_MAX_TOKENS = 16_000  # per model turn, thinking included
REVIEWER_THINKING = "medium"  # provider default is high; see reviewer_settings

_SYSTEM = """You are a senior hardware engineer auditing ONE part of a PCB \
schematic against its datasheet: {target}. (You are part of a review of the \
{scope}; other parts get their own audit.)

Here is the part, every pin, and what each pin connects to:
{part}

Work like an engineer, with your tools — never from memory:
1. search_datasheet for the pin description / pin functions table and for the \
typical application circuit; read_datasheet_pages for just those pages (text; \
as_pdf only when a table or drawing is unreadable). About 4–6 pages total.
2. Pin by pin: why is it connected this way, and is that what the datasheet \
requires? What is MISSING (a required capacitor, pull-up, termination, strap, \
bootstrap cap)? What is tied where it must not be? Is each pin's rail voltage \
within its rating?
3. Check values with calc() / divider_output() (e.g. a feedback divider against \
the datasheet's reference voltage).
4. Before calling something wrong or missing, trace the whole path with \
trace_pin / between / structures: jumpers and switches complete circuits on \
purpose, and datasheets often specify internal pull resistors. Unusual but \
valid choices are not findings.
5. Report each real issue once with report_finding as soon as you have \
confirmed it (don't save findings for the end — your steps are limited): the \
netlist facts it rests on (netlist_claims) and the datasheet page you read with \
a short verbatim quote. A wrong claim is refused — fix it and report again. \
Mind active-low pins (OE, EN, RESET written with a bar, #, or _N).

Severity: must-fix = the board won't work or will be damaged; verify = likely \
wrong or needs an engineer's call; cosmetic = harmless. Report nothing that is \
correct; invent nothing. If the datasheet can't be found, report one verify \
finding saying so and stop.

Be terse between tool calls: no narration, no restating tool output, no plans \
— just call the next tool. Spend words only in report_finding: write its detail \
and failure_mode as clear, complete sentences an engineer will read.

Finish with one sentence on what you checked.

Board context:
{board}
"""


class NetlistClaim(BaseModel):
    ref: str = Field(description="Part reference, e.g. U1")
    pin: str = Field(description="Pin number or name, e.g. 2 or EN")
    net: str = Field(description="The net you say that pin is on, e.g. +24V")


@dataclass
class ReviewerRun:
    """What one reviewer produced, plus its working state."""

    name: str
    findings: list[Finding] = field(default_factory=list)
    steps: int = 0


def _resolve_pin(design: Design, ref: str, pin: str):
    """The node for 'U1' + '2' or 'U1' + 'EN' (pin number or function)."""
    want = pin.strip().upper()
    for net in design.nets:
        for n in net.nodes:
            if n.ref.upper() != ref.strip().upper():
                continue
            if n.pin.upper() == want or (cm.pin_function(n) or "").upper() == want:
                return net, n
    return None, None


def check_claims(design: Design, claims: list[NetlistClaim]) -> list[str]:
    """Problems with the claims ([] = all true against the netlist)."""
    problems = []
    for c in claims:
        net, node = _resolve_pin(design, c.ref, c.pin)
        if node is None:
            problems.append(f"{c.ref} has no pin '{c.pin}'")
        elif net.name.lower() != c.net.strip().lower():
            problems.append(
                f"{c.ref}.{node.pin}({cm.pin_function(node) or c.pin}) is on net {net.name}, not {c.net}"
            )
    return problems


def quote_on_page(quote: str, page_text: str) -> bool:
    """The quote appears on the page (whitespace/hyphenation-insensitive), or
    nearly all of its words do — PDF text extraction mangles spacing."""
    q, p = normalize(quote), normalize(page_text)
    if len(q) >= 8 and q in p:
        return True
    words = [w for w in re.findall(r"[a-z0-9.]+", quote.lower()) if len(w) >= 3]
    if len(words) < 3:
        return False
    hits = sum(1 for w in words if w in p)
    return hits / len(words) >= 0.85


# --- safe arithmetic for calc() ---

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_VALUE_TOKEN = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?[pnuµμmkKMG]\d*)(?![\w.])")


def safe_eval(expr: str) -> float:
    """+ - * / ** and parentheses over numbers; '4.7k', '100n', '2k2' allowed."""
    expr = _VALUE_TOKEN.sub(lambda m: repr(parse_value(m.group(1))), expr)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("only numbers and + - * / ** ( ) are allowed")

    return ev(ast.parse(expr, mode="eval"))


def board_brief(cmap: cm.CircuitMap) -> str:
    """A compact orientation: rails, parts by kind, key structures."""
    by_kind: dict[str, list[str]] = {}
    for p in cmap.parts.values():
        by_kind.setdefault(p.kind, []).append(f"{p.ref} {p.value or ''}".strip())
    parts = "\n".join(f"  {k}: {', '.join(v)}" for k, v in sorted(by_kind.items()))
    return f"{cm.describe_rails(cmap)}\nParts:\n{parts}"


def _step_summary(tool: str, args: dict) -> str:
    """A human line for the live panel: 'reading TJA1051TK-3 p.5,6'."""
    a = args or {}
    if tool == "read_datasheet_pages":
        pages = ",".join(str(p) for p in a.get("pages", []))
        return f"reading {a.get('part', '?')} datasheet p.{pages}" + (" (as PDF)" if a.get("as_pdf") else "")
    if tool == "search_datasheet":
        return f"searching {a.get('part', '?')} datasheet for “{a.get('query', '')}”"
    if tool == "find_datasheet":
        return f"getting the {a.get('part', '?')} datasheet"
    if tool == "get_part":
        return f"inspecting {a.get('ref', '?')}"
    if tool == "get_net":
        return f"tracing net {a.get('name', '?')}"
    if tool == "trace_pin":
        return f"tracing {a.get('ref', '?')}.{a.get('pin', '?')}"
    if tool == "between":
        return f"what connects {a.get('a', '?')} and {a.get('b', '?')}"
    if tool in ("calc", "divider_output"):
        return "calculating " + (a.get("expression") or f"divider {a.get('r_top')}/{a.get('r_bottom')}")
    if tool == "report_finding":
        return f"reporting: {a.get('title', '')}"
    if tool == "structures":
        return f"listing {a.get('kind') or 'circuit'} structures"
    if tool == "rails":
        return "listing power rails"
    return tool


COMPACT_AT_CHARS = 30_000  # ~8k tokens of history before old tool output is trimmed
_KEEP_RECENT_REQUESTS = 3
_PDF_CHARS = 12_000  # rough per-attachment weight in the size estimate
_TRIMMED = "\n…[trimmed to save context — call the tool again if you need this]"


def _size(messages) -> int:
    from pydantic_ai.messages import BinaryContent, ToolReturnPart, UserPromptPart

    n = 0
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart):
                n += len(str(p.content))
            elif isinstance(p, UserPromptPart):
                items = p.content if isinstance(p.content, list) else [p.content]
                n += sum(_PDF_CHARS if isinstance(c, BinaryContent) else len(str(c)) for c in items)
            else:
                n += len(str(getattr(p, "content", "") or getattr(p, "args", "") or ""))
    return n


def compact_history(messages: list) -> list:
    """Once the history is large, trim old tool output (datasheet pages above all)
    to a stub, keeping the last few requests whole. Done in one batch so the
    trimmed prefix then stays stable and prompt caching keeps hitting."""
    from dataclasses import replace

    from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart, UserPromptPart

    if _size(messages) < COMPACT_AT_CHARS:
        return messages
    requests = [i for i, m in enumerate(messages) if isinstance(m, ModelRequest)]
    keep_from = requests[-_KEEP_RECENT_REQUESTS] if len(requests) > _KEEP_RECENT_REQUESTS else 0
    out = []
    for i, m in enumerate(messages):
        if i >= keep_from or not isinstance(m, ModelRequest) or i == 0:
            out.append(m)
            continue
        parts = []
        for p in m.parts:
            if isinstance(p, ToolReturnPart) and len(str(p.content)) > 600 and not str(p.content).endswith(_TRIMMED):
                p = replace(p, content=str(p.content)[:400] + _TRIMMED)
            elif isinstance(p, UserPromptPart) and isinstance(p.content, list) and any(
                isinstance(c, BinaryContent) for c in p.content
            ):
                p = replace(p, content=[
                    "[datasheet pages shown earlier — trimmed; read them again if needed]"
                    if isinstance(c, BinaryContent) else c
                    for c in p.content
                ])
            parts.append(p)
        out.append(replace(m, parts=parts))
    return out


def reviewer_settings(model_spec: str) -> dict:
    """Model settings for audit turns.

    Most audit turns are lookups (search, read, trace), so thinking runs at a
    reduced effort: thinking tokens are billed as output and were the bulk of a
    reviewer's output cost. Nobody sees reviewer thoughts, so they aren't
    requested back either. Models that don't think are left alone.
    """
    from pcb_assistant.providers import capability

    # Thinking and the reply share max_tokens; pydantic-ai's Anthropic default
    # (4096) runs out mid-audit and fails the whole reviewer.
    settings: dict = {"max_tokens": REPLY_MAX_TOKENS}
    try:
        thinks = capability(model_spec).supports_thinking
    except ValueError:
        thinks = False
    if thinks:
        settings["thinking"] = REVIEWER_THINKING
    return settings


def build_reviewer(
    model_spec: str,
    design: Design,
    cmap: cm.CircuitMap,
    sheets: DatasheetTools,
    run: ReviewerRun,
    target: str,
    progress: Progress | None = None,
    scope: str = "whole board",
):
    """An audit agent for one part (`target` is its ref, e.g. 'U3')."""
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import ProcessHistory

    from pcb_assistant.agent.agent import to_pai_model

    settings = reviewer_settings(model_spec)
    if model_spec.startswith("claude:"):
        settings["anthropic_cache"] = True  # each turn re-sends the history; cache it
    return Agent(
        to_pai_model(model_spec),
        system_prompt=reviewer_prompt(cmap, target, scope),
        model_settings=settings,
        capabilities=[ProcessHistory(compact_history)],
        toolsets=[build_reviewer_toolset(model_spec, design, cmap, sheets, run, progress)],
    )


def reviewer_prompt(cmap: cm.CircuitMap, target: str, scope: str = "whole board") -> str:
    """The audit instructions for one part, with the board brief and its connections."""
    part = cmap.parts.get(target)
    label = f"{target} {part.value or ''}".strip() if part else target
    return _SYSTEM.format(
        target=label, scope=scope, board=board_brief(cmap),
        part=cm.describe_part(cmap, target) if part else f"(no part {target} on this board)",
    )


def build_reviewer_toolset(
    model_spec: str,
    design: Design,
    cmap: cm.CircuitMap,
    sheets: DatasheetTools,
    run: ReviewerRun,
    progress: Progress | None = None,
):
    """A reviewer's audit tools. Shared by the pydantic-ai agent and the MCP bridge."""
    from pydantic_ai.toolsets import FunctionToolset

    toolset = FunctionToolset()

    @toolset.tool_plain
    def rails() -> str:
        """Power rails with their voltages."""
        return cm.describe_rails(cmap)

    @toolset.tool_plain
    def structures(kind: str = "") -> str:
        """Recognised circuit structures: divider, pull_up, pull_down, led_resistor,
        supply_caps, series, floating_pin. Empty kind = all."""
        return cm.describe_structures(cmap, kind or None)

    @toolset.tool_plain
    def get_part(ref: str) -> str:
        """Every pin of a part: function, electrical type, net, and what else is on that net."""
        return cm.describe_part(cmap, ref)

    @toolset.tool_plain
    def get_net(name: str) -> str:
        """Every pin on a net."""
        return cm.describe_net(cmap, name)

    @toolset.tool_plain
    def trace_pin(ref: str, pin: str) -> str:
        """The net one pin (number or name, e.g. 'EN') is on, and everything on it."""
        net, node = _resolve_pin(design, ref, pin)
        if node is None:
            return f"{ref} has no pin '{pin}'."
        return f"{ref}.{node.pin}({cm.pin_function(node) or '?'}): " + cm.describe_net(cmap, net.name)

    @toolset.tool_plain
    def between(a: str, b: str) -> str:
        """How two parts/nets connect: shared signal nets, or the parts sitting between them."""
        return cm.between(cmap, a, b)

    @toolset.tool_plain
    def find_datasheet(part: str) -> str:
        """Get a part's datasheet (ref like 'U3' or a part number)."""
        return sheets.find(part)

    @toolset.tool_plain
    def search_datasheet(part: str, query: str) -> str:
        """Pages of a part's datasheet about a pin, spec or section."""
        return sheets.search(part, query)

    @toolset.tool_plain
    def read_datasheet_pages(part: str, pages: list[int], as_pdf: bool = False):
        """Read up to 4 pages as text. as_pdf=true attaches up to 2 pages as images (tables,
        pinout drawings) - costly, use only when the text is unreadable."""
        return sheets.read(part, pages, as_pdf)

    @toolset.tool_plain
    def calc(expression: str) -> str:
        """Arithmetic: + - * / ** ( ). Component values allowed: 4.7k, 100n, 2k2."""
        try:
            v = safe_eval(expression)
        except (ValueError, SyntaxError, ZeroDivisionError) as e:
            return f"can't evaluate: {e}"
        return f"{expression} = {v:.6g} ({fmt(v)})"

    @toolset.tool_plain
    def divider_output(r_top: str, r_bottom: str, v_in: float) -> str:
        """Voltage at a divider's tap, and the input that gives a reference at the tap."""
        rt, rb = parse_value(r_top), parse_value(r_bottom)
        if not rt or not rb:
            return "give resistor values like '100k' and '19.1k'"
        ratio = rb / (rt + rb)
        return (
            f"tap = {v_in:g} V × {ratio:.5f} = {v_in * ratio:.4f} V. "
            f"(For a feedback divider: Vout = Vref × {1 / ratio:.4f}.)"
        )

    @toolset.tool_plain
    def report_finding(
        severity: Severity,
        title: str,
        detail: str,
        failure_mode: str,
        component_ref: str,
        netlist_claims: list[NetlistClaim],
        datasheet_part: str = "",
        page: int = 0,
        quote: str = "",
        net: str = "",
        ruled_out: str = "",
    ) -> str:
        """Record one issue. netlist_claims = the pin→net facts it rests on (checked).
        datasheet_part + page + quote = the datasheet evidence (page must have been
        read by you; the quote must be on it). ruled_out = for must-fix, the
        innocent explanation you checked and why it doesn't apply (e.g. a jumper
        or switch that completes the circuit, an internal pull resistor)."""
        if severity == "must-fix" and len(ruled_out.strip()) < 15:
            return (
                "NOT recorded — a must-fix needs ruled_out: what would make this "
                "connection intentional (a jumper/switch, an internal pull, a different "
                "operating mode) and why that isn't the case here. Check structures() for "
                "jumpers and switched parts first."
            )
        dup = _duplicate_of(run.findings, component_ref, title)
        if dup is not None:
            return f"Already recorded as '{dup.title}' — not added again."
        problems = check_claims(design, netlist_claims)
        if problems:
            return "NOT recorded — the netlist says otherwise: " + "; ".join(problems) + \
                ". Re-check with trace_pin and report again if the issue still stands."

        citation = excerpt = key = None
        cited_page = None
        if datasheet_part:
            from pcb_assistant.datasheets.identity import part_for

            part = part_for(design, datasheet_part)
            if part is None:
                return f"NOT recorded — '{datasheet_part}' has no datasheet on this board."
            if (part.key, page) not in sheets.ledger:
                return (
                    f"NOT recorded — you haven't read {part.key} datasheet p.{page}. "
                    "Read it with read_datasheet_pages, then report."
                )
            texts = sheets.lib.pages(part.key)
            if not quote or not quote_on_page(quote, texts[page - 1]):
                return (
                    f"NOT recorded — that quote isn't on {part.key} datasheet p.{page}. "
                    "Quote the page's own words (a sentence or table row)."
                )
            key, cited_page, excerpt = part.key, page, quote.strip()
            citation = f"{part.key} datasheet p.{page}"

        f = Finding(
            severity=severity, title=title.strip(), detail=detail.strip(),
            failure_mode=failure_mode.strip(), citation=citation, source="llm",
            connectivity_verified="verified" if netlist_claims else "not-applicable",
            component_ref=component_ref.strip() or None, net=net.strip() or None,
            datasheet=key, page=cited_page, excerpt=excerpt,
        )
        note = ""
        if severity == "must-fix":
            page_text = sheets.lib.pages(key)[cited_page - 1] if key and cited_page else ""
            verdict, reason = second_opinion(model_spec, cmap, f, ruled_out, page_text)
            if progress:
                progress({"reviewer": run.name, "status": "step", "tool": "second_opinion",
                          "summary": f"second opinion on '{f.title}': {verdict}"})
            if verdict != "confirmed":
                f.severity = "verify"
                f.labels.append("disputed")
                f.detail += f"\n\nSecond opinion ({verdict}): {reason}"
                note = f" — but a second check {verdict} it ({reason}), so it is recorded as verify."
        run.findings.append(f)
        if progress:
            progress({"reviewer": run.name, "status": "finding", "findings": len(run.findings),
                      "summary": f"{f.severity}: {f.title}"})
        if note:
            return "Recorded" + note
        return "Recorded" + (f" with evidence {citation}." if citation else " (no datasheet citation — it will be shown as needing verification).")

    return toolset


_SKEPTIC = """You are checking a colleague's schematic-review finding before it \
is marked MUST-FIX (board-killing). Be a skeptic: look for the reading under which \
the design is correct — an active-low pin, an internal pull resistor, a jumper or \
switch, a mode the designer chose on purpose, a misread table.

The finding:
  {title}
  {detail}
  Failure it claims: {failure}
  What the reviewer ruled out: {ruled_out}

The part and its connections (ground truth from the netlist):
{part}

The datasheet page the reviewer cited ({citation}):
{page}

Return ONLY JSON: {{"verdict": "confirmed" | "rejected" | "unsure", "reason": "<one sentence>"}}
"confirmed" only if the page and the netlist together show the board is broken as claimed.
"""


def second_opinion(
    model_spec: str, cmap: cm.CircuitMap, f: Finding, ruled_out: str, page_text: str
) -> tuple[str, str]:
    """An independent, history-free check of a must-fix. ('confirmed'|'rejected'|'unsure', why)."""
    from pcb_assistant.providers import ask
    from pcb_assistant.reasoning._json import extract_json_object

    prompt = _SKEPTIC.format(
        title=f.title, detail=f.detail, failure=f.failure_mode or "—", ruled_out=ruled_out or "—",
        part=cm.describe_part(cmap, f.component_ref) if f.component_ref else "(no part named)",
        citation=f.citation or "none", page=page_text[:8000] or "(no datasheet page cited)",
    )
    try:
        data = extract_json_object(ask(model_spec, prompt))
    except Exception as e:  # the check itself failed — don't grant must-fix on faith
        return "unsure", f"the second check couldn't run ({_short_error(e)})"
    verdict = str(data.get("verdict", "unsure")).lower()
    if verdict not in ("confirmed", "rejected", "unsure"):
        verdict = "unsure"
    return verdict, str(data.get("reason", "")).strip() or "no reason given"


_STOP = {"the", "a", "an", "is", "on", "of", "to", "in", "for", "and", "or", "not", "with", "at"}


def _duplicate_of(found: list[Finding], component_ref: str, title: str) -> Finding | None:
    """An already-recorded finding on the same part saying mostly the same thing."""
    words = {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _STOP}
    for f in found:
        if (f.component_ref or "").upper() != component_ref.strip().upper():
            continue
        other = {w for w in re.findall(r"[a-z0-9]+", f.title.lower()) if w not in _STOP}
        if words and other and len(words & other) / len(words | other) >= 0.4:
            return f
    return None


def _short_error(e: Exception) -> str:
    text = str(e)
    if "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        return "quota exhausted — free tiers allow too few requests for an agentic review"
    if "503" in text or "overloaded" in text.lower():
        return "the model is overloaded right now"
    return text[:200]


def run_reviewer(
    design: Design,
    model_spec: str,
    name: str,
    focus: str,
    project_dir: str | Path,
    progress: Progress | None = None,
    cmap: cm.CircuitMap | None = None,
    refs: list[str] | None = None,
) -> list[Finding]:
    """Run one reviewer to completion (sync; call from a worker thread).

    The reviewer audits each part in its scope with a fresh, short agent — one
    part, its datasheet, pin by pin — so context stays small and no part is
    skipped. `refs` is the scope; by default every part with a datasheet.
    """
    from pydantic_ai import UsageLimits
    from pydantic_ai.exceptions import UsageLimitExceeded
    from pydantic_ai.messages import FunctionToolCallEvent
    from pydantic_ai.usage import RunUsage

    from pcb_assistant.providers import capability

    cmap = cmap or cm.build_circuit_map(design)
    try:
        pdf_ok = capability(model_spec).supports_pdf
    except ValueError:
        pdf_ok = False
    sheets = DatasheetTools(project_dir, design, supports_pdf=pdf_ok)
    run = ReviewerRun(name=name)
    targets = refs if refs is not None else auditable_refs(design)
    from pcb_assistant.engines.config import engine_of

    if engine_of(model_spec) is not None:
        return _run_on_engine(design, model_spec, name, focus, project_dir, cmap, sheets, run, targets, progress)
    usage = RunUsage()  # the whole reviewer's cost, summed over its audits
    t0 = time.monotonic()

    for ref in targets:
        audit_usage = RunUsage()  # per audit: the request limit counts this, not the total
        if progress:
            progress({"reviewer": name, "status": "step", "tool": "audit",
                      "summary": f"auditing {ref} {(cmap.parts[ref].value or '') if ref in cmap.parts else ''}".strip()})
        agent = build_reviewer(model_spec, design, cmap, sheets, run, ref, progress, scope=focus)

        async def on_events(_ctx, events, ref=ref):
            async for ev in events:
                if isinstance(ev, FunctionToolCallEvent) and progress:
                    run.steps += 1
                    args = ev.part.args_as_dict() if hasattr(ev.part, "args_as_dict") else {}
                    progress({"reviewer": name, "status": "step", "tool": ev.part.tool_name,
                              "summary": f"{ref}: {_step_summary(ev.part.tool_name, args)}"})

        try:
            agent.run_sync(
                f"Audit {ref}.",
                usage_limits=UsageLimits(request_limit=REQUEST_LIMIT),
                event_stream_handler=on_events,
                usage=audit_usage,  # filled as it goes, so a cut-short audit still counts
            )
        except UsageLimitExceeded:
            run.findings.append(Finding(
                severity="verify",
                title=f"Audit of {ref} ran out of steps before finishing",
                detail=f"It stopped after {REQUEST_LIMIT} model calls; its findings are partial.",
                source="llm", component_ref=ref, labels=["unverified"],
            ))
        except Exception as e:  # provider error: keep what's found, skip the rest
            usage.incr(audit_usage)
            if not run.findings:
                _report_usage(progress, name, usage)
                raise
            log.warning("reviewer %s stopped at %s: %s", name, ref, e)
            run.findings.append(Finding(
                severity="verify",
                title=f"Reviewer '{name}' was cut off at {ref}",
                detail=f"The model provider failed ({_short_error(e)}); parts after {ref} weren't audited.",
                source="llm", labels=["unverified"],
            ))
            break
        usage.incr(audit_usage)

    _report_usage(progress, name, usage)
    _log_usage(project_dir, name, model_spec, len(targets), usage)
    log.info("reviewer %s: %d parts, %d findings, %d steps, %d requests, %d in / %d cached / %d out tokens, %.1fs",
             name, len(targets), len(run.findings), run.steps, usage.requests, usage.input_tokens,
             usage.cache_read_tokens, usage.output_tokens, time.monotonic() - t0)
    return run.findings


def _run_on_engine(
    design: Design,
    model_spec: str,
    name: str,
    focus: str,
    project_dir: str | Path,
    cmap: cm.CircuitMap,
    sheets: DatasheetTools,
    run: ReviewerRun,
    targets: list[str],
    progress: Progress | None,
) -> list[Finding]:
    """run_reviewer on a subscription CLI: the same per-part audits and tools,
    with the CLI running each audit's model loop through the MCP bridge.

    A spent usage window keeps what's found and stops — or, if the user opted in,
    carries on with the same vendor's API key for the remaining parts.
    """
    from pcb_assistant.engines import dispatch
    from pcb_assistant.engines.base import Usage, UsageLimitError
    from pcb_assistant.providers import capability
    from pcb_assistant.providers.registry import api_fallback

    # Audit turns are mostly lookups: reduced thinking, as on the API path.
    effort = "low" if capability(model_spec).supports_thinking else None
    usage = Usage()
    t0 = time.monotonic()
    for i, ref in enumerate(targets):
        if progress:
            progress({"reviewer": name, "status": "step", "tool": "audit",
                      "summary": f"auditing {ref} {(cmap.parts[ref].value or '') if ref in cmap.parts else ''}".strip()})

        def on_tool(ev, ref=ref):
            if ev["type"] == "tool_call" and progress:
                run.steps += 1
                progress({"reviewer": name, "status": "step", "tool": ev["name"],
                          "summary": f"{ref}: {_step_summary(ev['name'], ev.get('args') or {})}"})

        toolset = build_reviewer_toolset(model_spec, design, cmap, sheets, run, progress)
        try:
            out = dispatch.run(
                model_spec, reviewer_prompt(cmap, ref, focus), f"Audit {ref}.",
                toolset=toolset, on_tool=on_tool, max_tool_calls=REQUEST_LIMIT,
                effort=effort, persist=False,
            )
        except UsageLimitError as e:
            fallback = api_fallback(model_spec)
            _report_usage_engine(progress, name, usage)
            if fallback is not None:
                log.info("reviewer %s: subscription spent at %s, continuing on %s", name, ref, fallback)
                return run.findings + run_reviewer(
                    design, fallback, name, focus, project_dir, progress, cmap, targets[i:]
                )
            if not run.findings:
                raise
            run.findings.append(Finding(
                severity="verify",
                title=f"Reviewer '{name}' stopped at {ref}: subscription usage limit",
                detail=f"{e}\nParts after {ref} weren't audited. Re-run after the limit resets, "
                       "or switch to an API-key model.",
                source="llm", labels=["unverified"],
            ))
            break
        except Exception as e:
            _report_usage_engine(progress, name, usage)
            if not run.findings:
                raise
            log.warning("reviewer %s stopped at %s: %s", name, ref, e)
            run.findings.append(Finding(
                severity="verify",
                title=f"Reviewer '{name}' was cut off at {ref}",
                detail=f"The subscription CLI failed ({_short_error(e)}); parts after {ref} weren't audited.",
                source="llm", labels=["unverified"],
            ))
            break
        usage.add(out.usage)
        if (out.rate_limit or {}).get("step_limited"):
            run.findings.append(Finding(
                severity="verify",
                title=f"Audit of {ref} ran out of steps before finishing",
                detail=f"It stopped after {REQUEST_LIMIT} tool calls; its findings are partial.",
                source="llm", component_ref=ref, labels=["unverified"],
            ))

    _report_usage_engine(progress, name, usage)
    _log_usage(project_dir, name, model_spec, len(targets), usage)
    log.info("reviewer %s (subscription): %d parts, %d findings, %d steps, %.1fs",
             name, len(targets), len(run.findings), run.steps, time.monotonic() - t0)
    return run.findings


def _report_usage_engine(progress: Progress | None, name: str, usage) -> None:
    if progress:
        progress({"reviewer": name, "status": "usage", "requests": usage.requests,
                  "tokens_in": usage.tokens_in, "tokens_cached": usage.tokens_cached,
                  "tokens_cache_write": usage.tokens_cache_write, "tokens_out": usage.tokens_out})


def _report_usage(progress: Progress | None, name: str, usage) -> None:
    from pcb_assistant.agent.usage_log import from_run_usage

    _report_usage_engine(progress, name, from_run_usage(usage))


def _log_usage(project_dir: str | Path, name: str, model_spec: str, parts: int, usage) -> None:
    """One usage-log line per reviewer run (a RunUsage or an engine Usage)."""
    from pcb_assistant.agent import usage_log
    from pcb_assistant.engines.base import Usage

    if not isinstance(usage, Usage):
        usage = usage_log.from_run_usage(usage)
    usage_log.record(project_dir, "reviewer", usage, reviewer=name, model=model_spec, parts=parts)


def auditable_refs(design: Design) -> list[str]:
    """Parts worth a datasheet audit: everything that has a part number (ICs,
    protection diodes, smart LEDs). Passives are checked from the IC they serve."""
    from pcb_assistant.datasheets.identity import parts_needing_datasheets

    refs = [r for p in parts_needing_datasheets(design) for r in p.refs[:1]]
    # ICs first: they carry the requirements.
    return sorted(refs, key=lambda r: (not r.upper().startswith("U"), r))


_POWER_FUNCS = {"VIN", "VOUT", "SW", "FB", "PG", "PGOOD", "BST", "BOOT", "CB", "LX", "SS", "COMP", "ILIM"}


def split_power_signals(design: Design, cmap: cm.CircuitMap) -> tuple[list[str], list[str]]:
    """Medium depth: auditable parts split into the power path and everything else.
    Power = regulators (pins like VIN/VOUT/SW/FB) and protection between a rail and ground."""
    power, signals = [], []
    for ref in auditable_refs(design):
        part = cmap.parts.get(ref)
        funcs = {(p.function or "").upper() for p in part.pins} if part else set()
        classes = {p.net_class for p in part.pins} if part else set()
        is_reg = len(funcs & _POWER_FUNCS) >= 2
        is_rail_protection = part is not None and part.kind == "diode" and classes <= {"power", "ground"}
        (power if is_reg or is_rail_protection else signals).append(ref)
    return power, signals
