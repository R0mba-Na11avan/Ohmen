"""Review team: split the LLM review across several reviewers by board domain.

Depth (the Settings slider):
  low    — one reviewer over the whole board (the original single call).
  medium — two domain reviewers: power, and signals/interfaces.
  high   — a planner call splits the board into up to MAX_REVIEWERS blocks; one
           reviewer per block. Falls back to medium if the plan can't be parsed.

Every reviewer sees the full IR (cross-domain context stays intact) but is told
to focus on its block. Reviewers run concurrently; one failing becomes an
abstention for its block instead of sinking the review. Each finding is tagged
`reviewer:<name>` so the documenter (the chat agent) can report per reviewer.

Pass `progress` to watch the team work: it is called (from worker threads) with
{"reviewer", "focus", "status": planning|started|done|failed, "findings", "ms",
"error"} as each reviewer starts and finishes.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal

from pcb_assistant.findings.models import Finding
from pcb_assistant.ir.models import Design
from pcb_assistant.providers import ask
from pcb_assistant.reasoning import loop_b_review
from pcb_assistant.reasoning._json import extract_json_array

log = logging.getLogger(__name__)

ReviewDepth = Literal["low", "medium", "high"]
Progress = Callable[[dict[str, Any]], None]
MAX_REVIEWERS = 6

# (name, focus) — the focus is spliced into the reviewer prompt.
DOMAIN_REVIEWERS: list[tuple[str, str]] = [
    (
        "power",
        "power section: regulators (bucks, LDOs), input protection/TVS, rails, "
        "enable and power-good pins, bulk and decoupling capacitors",
    ),
    (
        "signals",
        "signal section: the MCU, buses and interfaces (CAN, USB, UART, SPI, I2C), "
        "level shifting, pull-ups/pull-downs, strapping and boot pins",
    ),
]

_PLAN_PROMPT = """You are the lead reviewer of a PCB schematic, given as a \
summary of its parts, rails and circuit structures. Split the board into at \
most {n} functional blocks so that each can be reviewed by a separate engineer (e.g. "buck converter", \
"CAN transceiver", "USB-C input", "MCU + boot/strapping"). Every component \
should belong to some block; small boards need fewer blocks.

Return ONLY a JSON array, one object per block:
[{{"name": "<short-slug>", "focus": "<what the reviewer covers, naming the \
parts by reference designator>"}}]

The board:

{board}
"""


def plan_reviewers(design: Design, model_spec: str) -> list[tuple[str, str]]:
    """High depth: ask the model to split the board. Clamped; medium on failure."""
    from pcb_assistant.analysis.circuit import build_circuit_map, describe_structures
    from pcb_assistant.reasoning.reviewer_agent import board_brief

    cmap = build_circuit_map(design)
    board = f"{board_brief(cmap)}\nStructures:\n{describe_structures(cmap)}"
    try:
        raw = ask(model_spec, _PLAN_PROMPT.format(n=MAX_REVIEWERS, board=board))
        items = extract_json_array(raw)
    except Exception as e:  # provider error or unusable reply: the fixed team still reviews
        log.warning("review planner failed, using the domain reviewers: %s", e)
        return DOMAIN_REVIEWERS
    out: list[tuple[str, str]] = []
    for d in items:
        if not isinstance(d, dict) or not str(d.get("focus", "")).strip():
            continue
        name = re.sub(r"[^a-z0-9]+", "-", str(d.get("name") or f"block-{len(out) + 1}").lower())
        out.append((name.strip("-") or f"block-{len(out) + 1}", str(d["focus"]).strip()))
    return out[:MAX_REVIEWERS] or DOMAIN_REVIEWERS


def blocks_from_understanding(
    design: Design, model_spec: str, project_dir: str | Path
) -> list[tuple[str, str]] | None:
    """High depth with a project: the board understanding's blocks are the split
    (one call that also answers chat's "why is this here?"). None on failure."""
    from pcb_assistant.analysis.understanding import understand

    try:
        u = understand(project_dir, design, model_spec)
    except Exception as e:
        log.warning("board understanding failed, falling back to the planner: %s", e)
        return None
    out = []
    for b in u.blocks[:MAX_REVIEWERS]:
        name = re.sub(r"[^a-z0-9]+", "-", b.name.lower()).strip("-") or f"block-{len(out) + 1}"
        out.append((name, f"{b.name} — {b.purpose} Parts: {', '.join(b.refs)}"))
    return out or None


def reviewers_for(
    design: Design, model_spec: str, depth: ReviewDepth, project_dir: str | Path | None = None
) -> list[tuple[str, str]]:
    if depth == "high":
        if project_dir is not None:
            blocks = blocks_from_understanding(design, model_spec, project_dir)
            if blocks:
                return blocks
        return plan_reviewers(design, model_spec)
    if depth == "medium":
        return DOMAIN_REVIEWERS
    return []


def _notify(progress: Progress | None, **event: Any) -> None:
    if progress is None:
        return
    try:
        progress(event)
    except Exception:  # a broken observer never sinks the review
        log.exception("review progress callback failed")


def _supports_tools(model_spec: str) -> bool:
    from pcb_assistant.providers import capability

    try:
        return capability(model_spec).supports_tools
    except ValueError:
        return False


def scope_refs(design: Design, name: str, focus: str | None) -> list[str] | None:
    """Which parts a reviewer audits. None = every part with a datasheet.

    power / signals split the board by role; a block or an explicit focus names
    its parts ('Parts: U1, L1', or 'the TJA1051') and those with datasheets are kept.
    """
    from pcb_assistant.analysis.circuit import build_circuit_map
    from pcb_assistant.reasoning.reviewer_agent import auditable_refs, split_power_signals

    if name in ("power", "signals"):
        power, signals = split_power_signals(design, build_circuit_map(design))
        return power if name == "power" else signals
    if not focus:
        return None
    auditable = auditable_refs(design)
    text = focus.upper()
    named = [
        r for r in auditable
        if re.search(rf"\b{re.escape(r.upper())}\b", text)
        or ((design.component(r) and design.component(r).value) or "").upper()[:6] in text
    ]
    if named:
        return named
    return [] if name != "board" else None


def _run_reviewer(
    design: Design,
    model_spec: str,
    name: str,
    focus: str | None,
    datasheet_pdf,
    progress: Progress | None = None,
    project_dir: str | Path | None = None,
    tag: bool = True,
) -> list[Finding]:
    """One reviewer over one block. Agentic (tools + datasheets) when the model can
    call tools and the project folder is known; otherwise the one-shot review."""
    _notify(progress, reviewer=name, focus=focus or "whole board", status="started")
    t0 = time.monotonic()
    try:
        if project_dir is not None and _supports_tools(model_spec):
            from pcb_assistant.reasoning.reviewer_agent import run_reviewer

            found = run_reviewer(
                design, model_spec, name, focus or "the whole board", project_dir,
                progress=lambda e: _notify(progress, **e),
                refs=scope_refs(design, name, focus),
            )
        else:
            found = loop_b_review.review_structured(design, model_spec, datasheet_pdf, focus)
    except Exception as e:  # provider/network failure — abstain for this block
        log.warning("reviewer %s failed: %s", name, e)
        _notify(progress, reviewer=name, status="failed", error=str(e),
                ms=int((time.monotonic() - t0) * 1000))
        return [
            Finding(
                severity="verify",
                title=f"Reviewer '{name}' could not finish",
                detail=f"{focus or 'whole board'}\n\n{e}",
                source="llm",
                labels=["unverified"] + ([f"reviewer:{name}"] if tag else []),
            )
        ]
    _notify(progress, reviewer=name, status="done", findings=len(found),
            ms=int((time.monotonic() - t0) * 1000))
    if tag:
        for f in found:
            f.labels.append(f"reviewer:{name}")
    return found


# On a subscription each reviewer is its own CLI process; past this many at
# once the machine does more work than the review gains.
SUB_MAX_PARALLEL = 3


def max_parallel(model_spec: str, team_size: int) -> int:
    """API reviewers all run at once; subscription reviewers at most SUB_MAX_PARALLEL."""
    from pcb_assistant.engines import config as engine_config

    if engine_config.engine_of(model_spec) is None:
        return max(1, team_size)
    return max(1, min(team_size, SUB_MAX_PARALLEL))


def team_review(
    design: Design,
    model_spec: str,
    depth: ReviewDepth = "low",
    datasheet_pdf: str | Path | None = None,
    focus: str | None = None,
    progress: Progress | None = None,
    project_dir: str | Path | None = None,
) -> list[Finding]:
    """LLM findings for the given depth (pre-dedup, pre-gate).

    An explicit `focus` (one part) is a single targeted review at any depth.
    With `project_dir`, tool-capable models review agentically (netlist tools +
    the project's datasheets); without it, each reviewer is one LLM call.
    """
    if focus or depth == "low":
        # One reviewer; untagged, so the low-depth output reads as before. It still
        # runs on its own thread: called from the chat agent's run_review tool, this
        # thread is inside that agent's run, and pydantic-ai refuses a nested
        # run_sync there. A fresh thread doesn't inherit that context.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run_reviewer, design, model_spec, "board", focus, datasheet_pdf,
                               progress, project_dir, tag=False).result()
    if depth == "high":
        _notify(progress, reviewer="planner", focus="splitting the board into blocks",
                status="planning")
    team = reviewers_for(design, model_spec, depth, project_dir)
    if depth == "high":
        _notify(progress, reviewer="planner", status="done", findings=0)
    log.info("review team (%s): %s", depth, ", ".join(n for n, _ in team))
    with ThreadPoolExecutor(max_workers=max_parallel(model_spec, len(team))) as pool:
        results = pool.map(
            lambda nf: _run_reviewer(design, model_spec, nf[0], nf[1], datasheet_pdf, progress,
                                     project_dir),
            team,
        )
        return [f for batch in results for f in batch]
