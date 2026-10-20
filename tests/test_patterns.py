"""Pattern runner: deterministically catches the injected bug, clean on control."""
from pcb_assistant.patterns.runner import load_patterns, run_patterns
from pcb_assistant.reasoning.core import deterministic_review


def test_seed_library_loads():
    pats = load_patterns()
    assert any(p.id == "lmr51625-buck" for p in pats)


def test_catches_floating_en(design_floating):
    fs = run_patterns(design_floating)
    en = [f for f in fs if "floating" in f.title.lower()]
    assert len(en) == 1
    f = en[0]
    assert f.severity == "must-fix"
    assert f.citation and "51625" in f.citation
    assert f.pattern_status == "trusted"
    assert f.connectivity_verified == "verified"
    assert f.component_ref == "U1"


def test_control_board_clean(design_correct):
    fs = run_patterns(design_correct)
    assert not any("floating" in f.title.lower() for f in fs)


def test_deterministic_review_priority_order(design_floating):
    fs = deterministic_review(design_floating)
    assert fs == sorted(fs, key=lambda f: f.rank)
    assert fs[0].severity == "must-fix"
