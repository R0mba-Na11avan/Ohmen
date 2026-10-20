"""Reasoning core orchestration.

The deterministic pattern path runs first (fast, cheap, trustworthy) and its
output goes through the citation gate. The LLM path (loop_b_review) fills gaps for
parts with no pattern; wiring its raw text into structured Findings is the next
step. Both feed the same gate + mute + log.
"""
from __future__ import annotations

import re
from pathlib import Path

from pcb_assistant.findings.models import Finding, sort_findings
from pcb_assistant.ir.models import Design
from pcb_assistant.patterns.runner import Pattern, run_patterns
from pcb_assistant.reasoning.review_team import Progress, ReviewDepth, team_review
from pcb_assistant.verify.verifier import apply_citation_gate

# Pin/signal tokens used to tell whether two findings are about the same thing.
_PIN_TOKEN = re.compile(
    r"\b(EN|VIN|VOUT|VCC|VIO|OE#?|CC1|CC2|CC|IO0|TXD|RXD|CANH|CANL|BOOT|FB|SW|S)\b",
    re.IGNORECASE,
)


def _tokens(title: str) -> set[str]:
    return {t.upper().rstrip("#") for t in _PIN_TOKEN.findall(title)}


def _reviewers(f: Finding) -> list[str]:
    return [label for label in f.labels if label.startswith("reviewer:")]


def _dedup(findings: list[Finding]) -> list[Finding]:
    """Drop duplicate LLM findings. Heuristic, conservative: two findings are the
    same issue only on a shared component_ref AND a shared pin/signal token.

    - An LLM finding covered by a pattern finding is dropped: patterns are
      verified against the netlist, so they win.
    - Two LLM findings (from different reviewers on a team review) merge into
      the more severe one, which carries both reviewer labels — agreement shows.
    """
    covered: set[tuple[str, str]] = set()
    for f in findings:
        if f.source == "pattern" and f.component_ref:
            for t in _tokens(f.title):
                covered.add((f.component_ref, t))

    out: list[Finding] = []
    seen: dict[tuple[str, str], Finding] = {}  # LLM (component, token) -> kept finding
    for f in findings:
        if f.source != "llm" or not f.component_ref:
            out.append(f)
            continue
        keys = [(f.component_ref, t) for t in _tokens(f.title)]
        if any(k in covered for k in keys):
            continue
        twin = next((seen[k] for k in keys if k in seen), None)
        if twin is None:
            out.append(f)
            for k in keys:
                seen[k] = f
            continue
        keep, drop = (f, twin) if f.rank < twin.rank else (twin, f)
        for label in _reviewers(drop):
            if label not in keep.labels:
                keep.labels.append(label)
        if keep is f:
            out[out.index(twin)] = f
            for k, v in list(seen.items()):
                if v is twin:
                    seen[k] = f
            for k in keys:
                seen[k] = f
    return out


def _project_checks(project_dir: str | Path | None) -> list[Finding]:
    if project_dir is None:
        return []
    from pcb_assistant.adapters.kicad_checks import kicad_checks
    from pcb_assistant.bom.fill import lifecycle_findings

    # BOM parts DigiKey says are going away ride along with the KiCad checks.
    return kicad_checks(project_dir) + lifecycle_findings(project_dir)


def deterministic_review(
    design: Design,
    patterns: list[Pattern] | None = None,
    project_dir: str | Path | None = None,
) -> list[Finding]:
    """Run the pattern library (plus KiCad ERC/DRC when the project folder is
    known), gate for citations, and priority-order the result."""
    findings = run_patterns(design, patterns) + _project_checks(project_dir)
    findings = apply_citation_gate(findings)
    return sort_findings(findings)


def review(
    design: Design,
    model_spec: str | None = None,
    datasheet_pdf: str | Path | None = None,
    focus: str | None = None,
    patterns: list[Pattern] | None = None,
    depth: ReviewDepth = "low",
    project_dir: str | Path | None = None,
    progress: Progress | None = None,
) -> list[Finding]:
    """Full Loop B review: deterministic patterns first (trusted, verified), then
    the LLM path (if a model is given) for gaps patterns don't cover. Both are put
    through the same citation gate and priority-ordered together.

    When a pattern and the LLM flag the same (component, signal), the LLM
    duplicate is dropped in favor of the netlist-verified pattern finding (see
    _dedup).

    `depth` sets how many LLM reviewers split the board (see review_team);
    `project_dir` adds KiCad's own ERC/DRC; `progress` watches the reviewers.
    """
    findings = run_patterns(design, patterns) + _project_checks(project_dir)
    if model_spec:
        findings = findings + team_review(
            design, model_spec, depth, datasheet_pdf, focus, progress, project_dir
        )
    findings = _dedup(findings)
    findings = apply_citation_gate(findings)
    return sort_findings(findings)
