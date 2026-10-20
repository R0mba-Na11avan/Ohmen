"""LLM review -> structured Findings, and the combined (patterns + LLM) review.

The model call is mocked; no network/API. Verifies parsing, coercion, and that
LLM findings pass through the same citation gate as pattern findings.
"""
from pcb_assistant.reasoning import core, loop_b_review


def test_to_finding_coerces_bad_severity():
    f = loop_b_review._to_finding({"severity": "blocker", "title": "x"})
    assert f.severity == "verify"  # unknown -> verify
    assert f.source == "llm"


def test_to_finding_maps_fields():
    f = loop_b_review._to_finding({
        "severity": "must-fix", "title": "Floating EN", "citation": "Table 5-1",
        "component_ref": "U1", "net": "unconnected-x", "failure_mode": "dead rail",
    })
    assert f.severity == "must-fix" and f.citation == "Table 5-1" and f.component_ref == "U1"


def test_review_structured_parses(monkeypatch, design):
    canned = '[{"severity":"cosmetic","title":"tidy silkscreen","component_ref":"U2"}]'
    monkeypatch.setattr(loop_b_review, "ask", lambda *a, **k: canned)
    fs = loop_b_review.review_structured(design, model_spec="claude:claude-sonnet-5")
    assert len(fs) == 1 and fs[0].source == "llm" and fs[0].severity == "cosmetic"


def test_review_structured_abstains_on_junk(monkeypatch, design):
    # A reply that ignores the format must not read as a clean board.
    monkeypatch.setattr(loop_b_review, "ask", lambda *a, **k: "sorry, no JSON here")
    fs = loop_b_review.review_structured(design, model_spec="claude:claude-sonnet-5")
    assert len(fs) == 1 and fs[0].severity == "verify" and "unverified" in fs[0].labels
    assert "sorry, no JSON here" in fs[0].detail


def test_local_junk_reply_names_the_context_window(monkeypatch, design):
    monkeypatch.setattr(loop_b_review, "ask", lambda *a, **k: "This netlist describes a board...")
    (f,) = loop_b_review.review_structured(design, model_spec="local:gemma4:e4b")
    assert "OLLAMA_CONTEXT_LENGTH" in f.detail


def test_combined_review_gates_uncited_llm_mustfix(monkeypatch, design_correct):
    # Pattern path is clean on the correct board; inject an uncited must-fix from the LLM.
    from pcb_assistant.findings.models import Finding

    fake = [Finding(severity="must-fix", title="scary but uncited", source="llm", component_ref="U9")]
    monkeypatch.setattr(loop_b_review, "review_structured", lambda *a, **k: fake)

    fs = core.review(design_correct, model_spec="claude:claude-sonnet-5")
    scary = [f for f in fs if f.title == "scary but uncited"][0]
    assert scary.severity == "verify"        # downgraded by the gate
    assert "unverified" in scary.labels


def test_review_without_model_is_patterns_only(design_floating):
    fs = core.review(design_floating, model_spec=None)
    assert fs and all(f.source == "pattern" for f in fs)


def test_dedup_drops_llm_duplicate_of_pattern():
    from pcb_assistant.findings.models import Finding

    pattern = Finding(severity="must-fix", title="U1 pin EN is floating", source="pattern",
                      component_ref="U1", citation="c", rule_id="lmr51625-buck:en-not-floating")
    llm_dup = Finding(severity="verify", title="U1 EN appears undriven", source="llm", component_ref="U1")
    llm_other = Finding(severity="verify", title="U3 S pin floating", source="llm", component_ref="U3")

    out = core._dedup([pattern, llm_dup, llm_other])
    assert pattern in out and llm_other in out and llm_dup not in out


def test_dedup_keeps_llm_when_different_component():
    from pcb_assistant.findings.models import Finding

    pattern = Finding(severity="must-fix", title="U1 pin EN is floating", source="pattern",
                      component_ref="U1", rule_id="r")
    llm = Finding(severity="verify", title="U2 EN floating", source="llm", component_ref="U2")
    assert llm in core._dedup([pattern, llm])
