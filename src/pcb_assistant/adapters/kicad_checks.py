"""KiCad's own rule checks (ERC on the schematic, DRC on the board) as Findings.

Both run headless through kicad-cli and emit JSON reports; the parsers here are
pure so they test without kicad-cli. Repeats of the same rule on the same part
(e.g. four hole-clearance hits on one connector) collapse into one finding, so
the mute key (rule + component) mutes them together.

    run_erc(project_dir)   # kicad-cli sch erc
    run_drc(project_dir)   # kicad-cli pcb drc (abstains when there is no board)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from pcb_assistant.adapters.kicad_netlist import _resolve_kicad_cli, find_project_schematic
from pcb_assistant.findings.models import Finding, Severity
from pcb_assistant.procenv import external_env

# "Symbol CR1 Pin 1 [...]" — power symbols/flags (#PWR05, #FLG01) aren't parts.
_SYMBOL_REF = re.compile(r"\bSymbol (?!#)(\S+)")
# "Pad B12 [GND] of J2 on F.Cu", "Segment of U4 on ...", "Reference field of CR1".
_FOOTPRINT_REF = re.compile(r"\bof ([A-Z]+[0-9]+[A-Z0-9_]*)\b")
_NET = re.compile(r"\[([^\],]+)\]")


def find_project_pcb(project_dir: str | Path) -> Path | None:
    """The board file next to the root schematic (`<pro stem>.kicad_pcb`), if any."""
    d = Path(project_dir)
    pros = sorted(d.glob("*.kicad_pro"))
    if len(pros) == 1:
        pcb = pros[0].with_suffix(".kicad_pcb")
        return pcb if pcb.exists() else None
    pcbs = sorted(d.glob("*.kicad_pcb"))
    return pcbs[0] if len(pcbs) == 1 else None


def _severity(kicad_severity: str, rule: str) -> Severity:
    if rule.startswith("silk"):  # silkscreen never kills a board
        return "cosmetic"
    return "must-fix" if kicad_severity == "error" else "verify"


def _group(violations: list[dict], tool: str, ref_re: re.Pattern) -> list[Finding]:
    """One Finding per (rule, component); the item descriptions become the detail."""
    groups: dict[tuple[str, str | None], list[dict]] = {}
    for v in violations:
        items = [i.get("description", "") for i in v.get("items", [])]
        ref = next((m.group(1) for d in items if (m := ref_re.search(d))), None)
        groups.setdefault((v.get("type", "unknown"), ref), []).append(v)

    out: list[Finding] = []
    for (rule, ref), vs in groups.items():
        first = vs[0]
        items = [i.get("description", "") for v in vs for i in v.get("items", [])]
        net = next((m.group(1) for d in items if (m := _NET.search(d))), None)
        worst = "error" if any(v.get("severity") == "error" for v in vs) else "warning"
        title = first.get("description", rule)
        if len(vs) > 1:
            title += f" (×{len(vs)})"
        out.append(
            Finding(
                severity=_severity(worst, rule),
                title=title,
                detail="\n".join(dict.fromkeys(items)),
                citation=f"KiCad {tool.upper()} rule {rule}",
                source=tool,  # type: ignore[arg-type]
                connectivity_verified="verified",
                component_ref=ref,
                net=net if tool == "drc" else None,
                rule_id=f"{tool}:{rule}",
            )
        )
    return out


def parse_erc_json(report: dict) -> list[Finding]:
    violations = [v for s in report.get("sheets", []) for v in s.get("violations", [])]
    return _group(violations, "erc", _SYMBOL_REF)


def parse_drc_json(report: dict) -> list[Finding]:
    violations = [
        *report.get("violations", []),
        *report.get("unconnected_items", []),
        *report.get("schematic_parity", []),
    ]
    return _group(violations, "drc", _FOOTPRINT_REF)


def _run(args: list[str]) -> dict:
    fd, out = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        r = subprocess.run(
            [*args, "--format", "json", "-o", out],
            capture_output=True, text=True, timeout=180, check=False, env=external_env(),
        )
        text = Path(out).read_text(encoding="utf-8") if os.path.getsize(out) else ""
        if not text:
            raise RuntimeError((r.stderr or r.stdout or "kicad-cli wrote no report").strip())
        return json.loads(text)
    finally:
        os.unlink(out)


def run_erc(project_dir: str | Path, kicad_cli: str | Path | None = None) -> list[Finding]:
    """KiCad ERC on the project's root schematic."""
    cli = _resolve_kicad_cli(kicad_cli)
    sch = find_project_schematic(project_dir)
    return parse_erc_json(
        _run([cli, "sch", "erc", "--severity-error", "--severity-warning", str(sch)])
    )


def run_drc(project_dir: str | Path, kicad_cli: str | Path | None = None) -> list[Finding]:
    """KiCad DRC (with schematic parity) on the project's board. [] if no board yet."""
    pcb = find_project_pcb(project_dir)
    if pcb is None:
        return []
    cli = _resolve_kicad_cli(kicad_cli)
    return parse_drc_json(_run([cli, "pcb", "drc", "--schematic-parity", str(pcb)]))


def not_run(tool: str, error: Exception) -> Finding:
    """The abstention that stands in for a check kicad-cli couldn't run."""
    return Finding(
        severity="verify",
        title=f"KiCad {tool.upper()} could not run",
        detail=str(error),
        source=tool,  # type: ignore[arg-type]
        rule_id=f"{tool}:not-run",
        labels=["unverified"],
    )


def kicad_checks(project_dir: str | Path) -> list[Finding]:
    """ERC + DRC, never raising: a check that can't run becomes an abstention."""
    out: list[Finding] = []
    for tool, fn in (("erc", run_erc), ("drc", run_drc)):
        try:
            out += fn(project_dir)
        except Exception as e:  # kicad-cli missing / failed / bad report
            out.append(not_run(tool, e))
    return out
