"""The project's current findings (`.pcb_assistant/last_review.json`).

Every run (chat review, chat check / ERC / DRC, the auto-check on save) merges
into this one file by source: a run replaces the findings of the sources it
covers and leaves the rest alone. So an ERC run drops ERC errors that were fixed
without wiping the AI review, and a design review replaces everything.

The file keeps every finding, dismissed ones included; dismissal (MuteStore) is
applied when it's read, so a restored finding comes straight back.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from pcb_assistant.findings.models import Finding, sort_findings
from pcb_assistant.jsonio import lock_for, read_json, write_json

DEFAULT_REL = Path(".pcb_assistant") / "last_review.json"

# Deterministic sources: they check everything every run, so a finding that
# disappears really was fixed.
RULE_SOURCES = frozenset({"pattern", "erc", "drc", "distributor"})
ALL_SOURCES = RULE_SOURCES | {"llm", "verifier"}


def _path(project_dir: str | Path) -> Path:
    return Path(project_dir) / DEFAULT_REL


def design_hash(design) -> str:
    """Content hash of the schematic, so a save that changed nothing isn't 'stale'."""
    return hashlib.sha1(design.model_dump_json().encode()).hexdigest()[:16]


def _read(project_dir: str | Path) -> dict:
    rev = read_json(_path(project_dir))
    if not isinstance(rev, dict):
        return {}
    if "runs" not in rev:  # older file: one run, visible findings only
        sources = ALL_SOURCES if rev.get("model", "patterns") != "patterns" else RULE_SOURCES
        rev["runs"] = {s: {"ts": rev.get("ts", 0.0), "hash": None} for s in sources}
        rev["review_model"] = "" if rev.get("model") == "patterns" else rev.get("model", "")
    return rev


def record_run(
    project_dir: str | Path,
    design,
    findings: list[Finding],
    sources: frozenset[str] | set[str],
    model: str = "",
    llm_scope: set[str] | None = None,
) -> None:
    """Merge one run's findings in: replace those of `sources`, keep the rest.

    `findings` are all of the run's findings, dismissed or not. A run that
    covers the AI review remembers which earlier AI findings it didn't raise
    again. `llm_scope` (a focused review's parts) limits that to those parts:
    AI findings on other parts are kept as they were.
    """
    from pcb_assistant.patterns.runner import pattern_coverage

    with lock_for(_path(project_dir)):
        _record_run(project_dir, design, findings, sources, model, llm_scope, pattern_coverage)


def _record_run(project_dir, design, findings, sources, model, llm_scope, pattern_coverage) -> None:
    rev = _read(project_dir)
    old = [Finding.model_validate(f) for f in rev.get("findings", [])]
    def replaced(f: Finding) -> bool:
        if f.source not in sources:
            return False
        return f.source != "llm" or llm_scope is None or f.component_ref in llm_scope

    kept = [f for f in old if not replaced(f)]
    rev["findings"] = [f.model_dump() for f in sort_findings(kept + findings)]

    now = time.time()
    digest = design_hash(design) if design is not None else None
    runs = rev.setdefault("runs", {})
    for s in sources:
        runs[s] = {"ts": now, "hash": digest}

    if "llm" in sources:
        raised = {f.mute_key for f in findings}
        rev["dropped"] = [
            f.model_dump() for f in old
            if f.source == "llm" and replaced(f) and f.mute_key not in raised
        ]
        rev["review_model"] = model
    if "pattern" in sources and design is not None:
        rev["coverage"] = pattern_coverage(design)
        rev["components"] = len(design.components)

    write_json(_path(project_dir), rev)


def last_rules_hash(project_dir: str | Path) -> str | None:
    """Design hash the rule checks last ran on (None = never, or unknown)."""
    return _read(project_dir).get("runs", {}).get("pattern", {}).get("hash")


def _group(runs: dict, sources, current: str | None) -> dict | None:
    ran = [runs[s] for s in sources if s in runs]
    if not ran:
        return None
    stale = current is not None and any(r.get("hash") not in (None, current) for r in ran)
    return {"ts": max(r.get("ts", 0.0) for r in ran), "stale": stale}


def load_review(project_dir: str | Path, design=None) -> dict:
    """The findings as the Findings tab shows them: visible vs dismissed, plus
    when the rule checks and the AI review last ran and whether the schematic
    changed since (only known when `design` is given)."""
    from pcb_assistant.findings.mute import MuteStore

    rev = _read(project_dir)
    if not rev:
        return {"findings": [], "dismissed": [], "muted": 0, "model": "", "ts": 0.0,
                "coverage": [], "components": 0, "runs": {}, "dropped": []}
    mutes = MuteStore.for_project(project_dir)
    visible, dismissed = mutes.split([Finding.model_validate(f) for f in rev.get("findings", [])])
    dropped = [d for d in rev.get("dropped", []) if not mutes.is_muted(d.get("mute_key", ""))]

    runs = rev.get("runs", {})
    current = design_hash(design) if design is not None else None
    groups = {
        "rules": _group(runs, RULE_SOURCES, current),
        "review": _group(runs, {"llm"}, current),
    }
    if groups["review"]:
        groups["review"]["model"] = rev.get("review_model", "")
    groups = {k: v for k, v in groups.items() if v}

    return {
        "findings": [f.model_dump() for f in visible],
        "dismissed": [f.model_dump() for f in dismissed],
        "muted": len(dismissed),
        "model": rev.get("review_model", ""),
        "ts": max((g["ts"] for g in groups.values()), default=0.0),
        "coverage": rev.get("coverage", []),
        "components": rev.get("components", 0),
        "runs": groups,
        "dropped": dropped,
    }
