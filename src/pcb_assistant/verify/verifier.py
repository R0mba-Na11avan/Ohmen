"""The citation gate + deterministic connectivity confirmation."""
from __future__ import annotations

from pcb_assistant.findings.models import Finding, Verified
from pcb_assistant.ir.models import Design


def apply_citation_gate(findings: list[Finding]) -> list[Finding]:
    """Enforce the verification floor on a list of findings (returns new copies).

    Policy (locked): no unproven claim may carry must-fix authority.
      * a provisional (AI-proposed) pattern finding is capped at 'verify';
      * a must-fix with no specific citation is downgraded to 'verify' and
        labeled 'unverified' — visible, but not authoritative.
    Nothing is dropped; abstention stays first-class.
    """
    out: list[Finding] = []
    for original in findings:
        f = original.model_copy(deep=True)

        if f.pattern_status == "provisional" and f.severity == "must-fix":
            f.severity = "verify"
            _label(f, "AI-proposed")

        if f.severity == "must-fix" and not f.citation:
            f.severity = "verify"
            _label(f, "unverified")

        out.append(f)
    return out


def _label(f: Finding, label: str) -> None:
    if label not in f.labels:
        f.labels.append(label)


def confirm_pin_net(
    design: Design, ref: str, pin: str, expected_net: str | None = None
) -> tuple[Verified, str | None]:
    """Confirm what a pin actually connects to, against the netlist (ground truth).

    Returns (status, actual_net_name). If `expected_net` is given, 'verified' means
    the pin's real net matches it (case-insensitive); otherwise 'verified' just
    means the pin is connected to *some* net. Turns "I think EN is driven" into
    "EN net = <name> (verified)".
    """
    net = design.net_of_pin(ref, pin)
    actual = net.name if net else None
    if net is None:
        return "unverified", None
    if expected_net is None:
        return "verified", actual
    matches = actual is not None and actual.lower() == expected_net.lower()
    return ("verified" if matches else "unverified"), actual
