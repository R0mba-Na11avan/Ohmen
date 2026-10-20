"""Finding identity, sorting, mute persistence, and the merged current findings."""
import json

from pcb_assistant.findings.current import (
    ALL_SOURCES,
    RULE_SOURCES,
    load_review,
    record_run,
)
from pcb_assistant.findings.models import Finding, sort_findings
from pcb_assistant.findings.mute import MuteStore
from pcb_assistant.ir.models import Design
from tests.conftest import IR_CORRECT, IR_FLOATING


def _f(**kw) -> Finding:
    base = dict(severity="verify", title="t", source="pattern")
    base.update(kw)
    return Finding(**base)


def test_mute_key_uses_rule_and_component_not_net():
    # Net is excluded: a floating pin's 'unconnected-(...)' net is volatile.
    f = _f(rule_id="lmr51625-buck:en-not-floating", component_ref="U1",
           net="unconnected-(U1-EN-Pad2)")
    assert f.mute_key == "lmr51625-buck:en-not-floating#U1"


def test_mute_key_stable_for_llm_findings():
    a = _f(source="llm", title="Same", component_ref="U1")
    b = _f(source="llm", title="Same", component_ref="U1")
    assert a.mute_key == b.mute_key and a.mute_key.startswith("llm:")


def test_sort_priority():
    fs = [_f(severity="cosmetic"), _f(severity="must-fix", citation="c"), _f(severity="verify")]
    assert [f.severity for f in sort_findings(fs)] == ["must-fix", "verify", "cosmetic"]


def test_mute_persists_and_splits(tmp_path):
    f = _f(rule_id="r1", component_ref="U1")
    store = MuteStore.for_project(tmp_path)
    visible, muted = store.split([f])
    assert visible and not muted

    store.mute(f.mute_key, reason="false positive on this board")
    # Reload from disk to prove persistence.
    reloaded = MuteStore.for_project(tmp_path)
    visible2, muted2 = reloaded.split([f])
    assert not visible2 and muted2

    reloaded.unmute(f.mute_key)
    assert MuteStore.for_project(tmp_path).split([f])[0]  # visible again


def _load(path) -> Design:
    return Design.model_validate_json(path.read_text(encoding="utf-8"))


def _titles(rev) -> list[str]:
    return sorted(f["title"] for f in rev["findings"])


def test_a_run_replaces_only_its_own_sources(tmp_path):
    d = _load(IR_FLOATING)
    record_run(tmp_path, d, [
        _f(title="erc A", source="erc", rule_id="erc:a"),
        _f(title="drc B", source="drc", rule_id="drc:b"),
        _f(title="ai C", source="llm", component_ref="U1"),
    ], ALL_SOURCES, "claude:x")

    # ERC re-ran and A is gone: fixed. DRC and the AI review are untouched.
    record_run(tmp_path, d, [], {"erc"})
    assert _titles(load_review(tmp_path, d)) == ["ai C", "drc B"]

    # The rule checks re-ran: the AI finding still stands.
    record_run(tmp_path, d, [_f(title="pattern P", rule_id="p")], RULE_SOURCES)
    assert _titles(load_review(tmp_path, d)) == ["ai C", "pattern P"]


def test_a_review_wipes_old_ai_findings_and_lists_the_ones_not_raised_again(tmp_path):
    d = _load(IR_FLOATING)
    record_run(tmp_path, d, [
        _f(title="ai old", source="llm", component_ref="U1"),
        _f(title="ai kept", source="llm", component_ref="U2"),
    ], ALL_SOURCES, "claude:x")
    record_run(tmp_path, d, [
        _f(title="ai kept", source="llm", component_ref="U2"),
        _f(title="ai new", source="llm", component_ref="U3"),
    ], ALL_SOURCES, "claude:y")

    rev = load_review(tmp_path, d)
    assert _titles(rev) == ["ai kept", "ai new"]
    assert [f["title"] for f in rev["dropped"]] == ["ai old"]
    assert rev["model"] == rev["runs"]["review"]["model"] == "claude:y"


def test_a_focused_review_only_replaces_ai_findings_on_its_parts(tmp_path):
    d = _load(IR_FLOATING)
    record_run(tmp_path, d, [
        _f(title="ai U1", source="llm", component_ref="U1"),
        _f(title="ai U2", source="llm", component_ref="U2"),
    ], ALL_SOURCES, "claude:x")
    record_run(tmp_path, d, [], ALL_SOURCES, "claude:x", llm_scope={"U1"})

    rev = load_review(tmp_path, d)
    assert _titles(rev) == ["ai U2"]
    assert [f["title"] for f in rev["dropped"]] == ["ai U1"]


def test_runs_go_stale_when_the_schematic_changes(tmp_path):
    before, after = _load(IR_FLOATING), _load(IR_CORRECT)
    record_run(tmp_path, before, [_f(title="ai", source="llm")], ALL_SOURCES, "claude:x")
    record_run(tmp_path, after, [], RULE_SOURCES)

    runs = load_review(tmp_path, after)["runs"]
    assert runs["rules"]["stale"] is False
    assert runs["review"]["stale"] is True


def test_dismissed_findings_are_kept_and_listed(tmp_path):
    f = _f(title="noisy", rule_id="r1", component_ref="U1")
    record_run(tmp_path, None, [f], RULE_SOURCES)
    MuteStore.for_project(tmp_path).mute(f.mute_key)

    rev = load_review(tmp_path)
    assert rev["findings"] == [] and rev["muted"] == 1
    assert rev["dismissed"][0]["title"] == "noisy"


def test_reads_a_last_review_from_before_runs_were_tracked(tmp_path):
    legacy = {
        "findings": [_f(title="ai", source="llm").model_dump()],
        "muted": 0, "model": "claude:x", "ts": 123.0, "coverage": [], "components": 3,
    }
    p = tmp_path / ".pcb_assistant" / "last_review.json"
    p.parent.mkdir()
    p.write_text(json.dumps(legacy), encoding="utf-8")

    rev = load_review(tmp_path, _load(IR_FLOATING))
    assert _titles(rev) == ["ai"] and rev["ts"] == 123.0
    assert rev["runs"]["review"] == {"ts": 123.0, "stale": False, "model": "claude:x"}
