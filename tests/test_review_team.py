"""Review team (Settings depth slider): who reviews, fallbacks, and merging.
No API calls — `ask` and the reviewer call are stubbed."""
import json
import threading

from pcb_assistant.findings.models import Finding
from pcb_assistant.reasoning import core, loop_b_review, review_team
from pcb_assistant.reasoning.review_team import DOMAIN_REVIEWERS, MAX_REVIEWERS, team_review


def _record(monkeypatch, make=lambda focus: []):
    """Stub the reviewer call; return the list of focuses it was called with."""
    calls: list[str | None] = []
    lock = threading.Lock()

    def fake(design, model_spec, datasheet_pdf=None, focus=None):
        with lock:
            calls.append(focus)
        return make(focus)

    monkeypatch.setattr(loop_b_review, "review_structured", fake)
    return calls


def test_low_is_one_whole_board_review(design, monkeypatch):
    calls = _record(monkeypatch)
    team_review(design, "claude:x", "low")
    assert calls == [None]


def test_low_runs_outside_the_calling_agent_run(design, monkeypatch):
    """The chat agent's run_review tool calls this from inside its own run, where
    pydantic-ai forbids a nested run_sync. The reviewer must not see that flag."""
    from pydantic_ai import _utils

    seen: list[bool] = []

    def fake(design, model_spec, datasheet_pdf=None, focus=None):
        seen.append(_utils._in_sync_callback.get())
        return []

    monkeypatch.setattr(loop_b_review, "review_structured", fake)
    token = _utils._in_sync_callback.set(True)
    try:
        team_review(design, "claude:x", "low")
    finally:
        _utils._in_sync_callback.reset(token)
    assert seen == [False]


def test_medium_is_power_and_signals(design, monkeypatch):
    calls = _record(monkeypatch)
    team_review(design, "claude:x", "medium")
    assert sorted(calls) == sorted(focus for _, focus in DOMAIN_REVIEWERS)


def test_explicit_focus_is_single_reviewer_at_any_depth(design, monkeypatch):
    calls = _record(monkeypatch)
    team_review(design, "claude:x", "high", focus="LMR51625")
    assert calls == ["LMR51625"]


def test_high_clamps_planner_to_max(design, monkeypatch):
    blocks = [{"name": f"Block {i}", "focus": f"part U{i}"} for i in range(9)]
    monkeypatch.setattr(review_team, "ask", lambda *a, **k: json.dumps(blocks))
    calls = _record(monkeypatch)
    team_review(design, "claude:x", "high")
    assert len(calls) == MAX_REVIEWERS


def test_high_falls_back_to_medium_on_bad_plan(design, monkeypatch):
    monkeypatch.setattr(review_team, "ask", lambda *a, **k: "no json, sorry")
    calls = _record(monkeypatch)
    team_review(design, "claude:x", "high")
    assert sorted(calls) == sorted(focus for _, focus in DOMAIN_REVIEWERS)


def test_failed_reviewer_abstains_others_survive(design, monkeypatch):
    def make(focus):
        if focus.startswith("power"):
            raise RuntimeError("rate limited")
        return [Finding(severity="verify", title="pull-up missing", source="llm")]

    _record(monkeypatch, make)
    out = team_review(design, "claude:x", "medium")
    titles = {f.title for f in out}
    assert "pull-up missing" in titles
    assert "Reviewer 'power' could not finish" in titles
    assert {"reviewer:signals"} <= {l for f in out for l in f.labels}


def test_cross_reviewer_duplicates_merge_and_keep_both_labels(design, monkeypatch):
    def make(focus):
        sev = "must-fix" if focus.startswith("signal") else "verify"
        return [Finding(severity=sev, title="U3 TXD floating", source="llm",
                        component_ref="U3", citation="Table 7-1")]

    _record(monkeypatch, make)
    out = core.review(design, model_spec="claude:x", depth="medium", patterns=[])
    u3 = [f for f in out if f.component_ref == "U3"]
    assert len(u3) == 1
    assert u3[0].severity == "must-fix"
    assert {"reviewer:power", "reviewer:signals"} <= set(u3[0].labels)


def _watch():
    events: list[dict] = []
    lock = threading.Lock()

    def progress(e):
        with lock:
            events.append(e)

    return events, progress


def test_progress_reports_each_reviewer_start_and_finish(design, monkeypatch):
    _record(monkeypatch, lambda focus: [Finding(severity="verify", title="x", source="llm")])
    events, progress = _watch()
    team_review(design, "claude:x", "medium", progress=progress)
    by = {(e["reviewer"], e["status"]) for e in events}
    assert by == {("power", "started"), ("power", "done"), ("signals", "started"), ("signals", "done")}
    done = [e for e in events if e["status"] == "done"]
    assert all(e["findings"] == 1 and e["ms"] >= 0 for e in done)
    starts = [e for e in events if e["status"] == "started"]
    assert all(e["focus"] for e in starts)


def test_progress_high_shows_planner_then_blocks(design, monkeypatch):
    blocks = [{"name": "buck", "focus": "U1"}, {"name": "can", "focus": "U3"}]
    monkeypatch.setattr(review_team, "ask", lambda *a, **k: json.dumps(blocks))
    _record(monkeypatch)
    events, progress = _watch()
    team_review(design, "claude:x", "high", progress=progress)
    assert events[0] == {"reviewer": "planner", "focus": "splitting the board into blocks",
                         "status": "planning"}
    assert {e["reviewer"] for e in events} == {"planner", "buck", "can"}


def test_progress_failed_reviewer_reported(design, monkeypatch):
    def make(focus):
        raise RuntimeError("rate limited")

    _record(monkeypatch, make)
    events, progress = _watch()
    team_review(design, "claude:x", "medium", progress=progress)
    failed = [e for e in events if e["status"] == "failed"]
    assert len(failed) == 2 and all("rate limited" in e["error"] for e in failed)


def test_low_depth_reports_one_board_reviewer(design, monkeypatch):
    _record(monkeypatch)
    events, progress = _watch()
    team_review(design, "claude:x", "low", progress=progress)
    assert [(e["reviewer"], e["status"]) for e in events] == [("board", "started"), ("board", "done")]


def test_broken_progress_callback_never_sinks_review(design, monkeypatch):
    _record(monkeypatch, lambda focus: [Finding(severity="verify", title="x", source="llm")])

    def boom(_):
        raise ValueError("observer bug")

    assert len(team_review(design, "claude:x", "medium", progress=boom)) == 2
