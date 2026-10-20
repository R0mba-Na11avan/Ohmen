"""Citation gate + connectivity confirmation."""
from pcb_assistant.findings.models import Finding
from pcb_assistant.verify.verifier import apply_citation_gate, confirm_pin_net


def _mf(**kw) -> Finding:
    base = dict(severity="must-fix", title="x", source="pattern")
    base.update(kw)
    return Finding(**base)


def test_uncited_mustfix_downgraded():
    out = apply_citation_gate([_mf(citation=None)])[0]
    assert out.severity == "verify"
    assert "unverified" in out.labels


def test_cited_trusted_mustfix_survives():
    out = apply_citation_gate([_mf(citation="Table 5-1", pattern_status="trusted")])[0]
    assert out.severity == "must-fix"
    assert "unverified" not in out.labels


def test_provisional_capped_at_verify():
    out = apply_citation_gate([_mf(citation="Table 5-1", pattern_status="provisional")])[0]
    assert out.severity == "verify"
    assert "AI-proposed" in out.labels


def test_gate_does_not_mutate_input():
    src = _mf(citation=None)
    apply_citation_gate([src])
    assert src.severity == "must-fix"  # original untouched


def test_confirm_pin_net(design):
    status, actual = confirm_pin_net(design, "U1", "2", expected_net="+24V")
    assert status == "verified" and actual == "+24V"
    status2, _ = confirm_pin_net(design, "U1", "2", expected_net="GND")
    assert status2 == "unverified"
