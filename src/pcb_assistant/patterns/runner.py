"""Declarative pattern schema + the generic runner.

Rule vocabulary (starts small; grow it only when a genuinely new KIND of check
appears — the individual patterns stay data):

  pin_connected      a pin (by number or function) must be driven (not floating)
  pin_not_connected  a pin must be a deliberate no-connect (strap/NC intent)
  pins_share_net     two pins of the part must sit on the same net (e.g. EN->VIN)
  component_between   a part whose value matches must bridge two of the pins' nets

Every finding a pattern emits is deterministic (checked against the netlist), so
its connectivity is 'verified' by construction and it carries the pattern's
citation + status (trusted/provisional).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from pcb_assistant.findings.models import Finding, PatternStatus, Severity
from pcb_assistant.ir.models import Design, Net

LIBRARY_DIR = Path(__file__).resolve().parent / "library"

RuleName = Literal[
    "pin_connected", "pin_not_connected", "pins_share_net", "component_between",
    "pin_on_net",
]


class Match(BaseModel):
    value_contains: str | None = None
    ref_prefix: str | None = None


class Check(BaseModel):
    id: str
    rule: RuleName
    severity: Severity = "verify"
    citation: str | None = None
    failure_mode: str = ""
    detail: str = ""
    # rule params (only the relevant ones are set per rule)
    pin: str | None = None
    pinfunction: str | None = None
    other_pin: str | None = None
    other_pinfunction: str | None = None
    value_contains: str | None = None
    expect_net_contains: str | None = None


class Pattern(BaseModel):
    id: str
    part: str = ""
    match: Match
    status: PatternStatus = "trusted"
    datasheet: str | None = None
    checks: list[Check] = Field(default_factory=list)


# ---------- loading ----------

def load_patterns(directory: str | Path = LIBRARY_DIR) -> list[Pattern]:
    """Load every *.yaml pattern in a directory."""
    directory = Path(directory)
    patterns: list[Pattern] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data:
            patterns.append(Pattern.model_validate(data))
    return patterns


# ---------- connectivity primitives ----------

def _matches(design: Design, m: Match) -> list[str]:
    refs: list[str] = []
    for c in design.components:
        if m.value_contains and m.value_contains.lower() not in (c.value or "").lower():
            continue
        if m.ref_prefix and not c.ref.startswith(m.ref_prefix):
            continue
        refs.append(c.ref)
    return refs


def _fn_base(fn: str | None) -> str:
    """Normalize a KiCad pin function like 'EN_2' -> 'EN' (strip a trailing _<pin>)."""
    if not fn:
        return ""
    head, _, tail = fn.rpartition("_")
    return head if (head and tail.isdigit()) else fn


def _net_of(design: Design, ref: str, pin: str | None, pinfunction: str | None) -> Net | None:
    """The net a pin connects to, addressed by pin number OR pin function.

    Function match is suffix-insensitive: 'EN' matches KiCad's 'EN_2'.
    """
    if pin is not None:
        return design.net_of_pin(ref, pin)
    if pinfunction is not None:
        want = pinfunction.lower()
        for net in design.nets:
            if any(n.ref == ref and _fn_base(n.pinfunction).lower() == want for n in net.nodes):
                return net
    return None


def _is_floating(net: Net | None, ref: str) -> bool:
    """A pin is floating if it has no net, sits on a KiCad 'unconnected-' net,
    or is alone on its net (nothing else to drive it)."""
    if net is None:
        return True
    if net.name.lower().startswith("unconnected-"):
        return True
    others = [n for n in net.nodes if n.ref != ref]
    return len(others) == 0


# ---------- rule evaluation ----------

def _finding(pat: Pattern, chk: Check, ref: str, title: str, net: str | None) -> Finding:
    return Finding(
        severity=chk.severity,
        title=title,
        detail=chk.detail,
        failure_mode=chk.failure_mode,
        citation=chk.citation,
        source="pattern",
        pattern_status=pat.status,
        connectivity_verified="verified",
        component_ref=ref,
        net=net,
        rule_id=f"{pat.id}:{chk.id}",
    )


def _eval_check(design: Design, pat: Pattern, chk: Check, ref: str) -> Finding | None:
    net = _net_of(design, ref, chk.pin, chk.pinfunction)
    label = chk.pinfunction or chk.pin or "?"

    if chk.rule == "pin_connected":
        if _is_floating(net, ref):
            return _finding(pat, chk, ref, f"{ref} pin {label} is floating / undriven", net.name if net else None)
        return None

    if chk.rule == "pin_not_connected":
        if not _is_floating(net, ref):
            return _finding(pat, chk, ref, f"{ref} pin {label} should be a no-connect but is wired to {net.name if net else '?'}", net.name if net else None)
        return None

    if chk.rule == "pins_share_net":
        other = _net_of(design, ref, chk.other_pin, chk.other_pinfunction)
        other_label = chk.other_pinfunction or chk.other_pin or "?"
        same = net is not None and other is not None and net.name == other.name
        if not same:
            return _finding(pat, chk, ref, f"{ref} pin {label} is not on the same net as pin {other_label}", net.name if net else None)
        return None

    if chk.rule == "pin_on_net":
        want = (chk.expect_net_contains or "").lower()
        if net is None:
            return _finding(pat, chk, ref, f"{ref} pin {label} is not connected (expected a net containing '{chk.expect_net_contains}')", None)
        if want and want not in net.name.lower():
            return _finding(pat, chk, ref, f"{ref} pin {label} is on '{net.name}', expected a net containing '{chk.expect_net_contains}'", net.name)
        return None

    if chk.rule == "component_between":
        # A part whose value matches must touch this pin's net.
        if net is None:
            return _finding(pat, chk, ref, f"{ref} pin {label} has no net for a required {chk.value_contains}", None)
        touching = {n.ref for n in net.nodes}
        ok = any(
            c.ref in touching and chk.value_contains
            and chk.value_contains.lower() in (c.value or "").lower()
            for c in design.components
        )
        if not ok:
            return _finding(pat, chk, ref, f"{ref} pin {label} net '{net.name}' is missing a {chk.value_contains}", net.name)
        return None

    return None


def run_patterns(design: Design, patterns: list[Pattern] | None = None) -> list[Finding]:
    """Run every applicable pattern against the design. Deterministic."""
    if patterns is None:
        patterns = load_patterns()
    findings: list[Finding] = []
    for pat in patterns:
        for ref in _matches(design, pat.match):
            for chk in pat.checks:
                f = _eval_check(design, pat, chk, ref)
                if f is not None:
                    findings.append(f)
    return findings


def pattern_coverage(design: Design, patterns: list[Pattern] | None = None) -> list[dict]:
    """Which placed parts the pattern library checked, and with how many checks.

    One entry per (pattern, matched ref). Parts with no pattern are absent — the
    report shows them as uncovered rather than implying they passed.
    """
    patterns = patterns if patterns is not None else load_patterns()
    out: list[dict] = []
    for pat in patterns:
        for ref in _matches(design, pat.match):
            out.append({"ref": ref, "part": pat.part or pat.id, "checks": len(pat.checks)})
    return sorted(out, key=lambda c: c["ref"])
