"""Board understanding + the agentic reviewer's evidence gate. No API: the
reviewer's model is a scripted FunctionModel."""
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from pcb_assistant.analysis import circuit as cm
from pcb_assistant.analysis.values import parse_value, voltage_from_name
from pcb_assistant.datasheets.identity import part_for
from pcb_assistant.datasheets.store import DatasheetLibrary
from pcb_assistant.reasoning import reviewer_agent as ra
from tests.test_datasheets import make_pdf


@pytest.mark.parametrize("s,v", [
    ("4.7k", 4700), ("2k2", 2200), ("100n", 1e-7), ("10uF", 1e-5), ("1μ", 1e-6),
    ("19.1k", 19100), ("4R7", 4.7), ("1M", 1e6), ("10m", 0.01), ("LED", None),
])
def test_parse_value(s, v):
    assert parse_value(s) == pytest.approx(v) if v is not None else parse_value(s) is None


def test_voltage_from_name():
    assert voltage_from_name("+3.3V") == 3.3 and voltage_from_name("3V3") == 3.3
    assert voltage_from_name("+24V") == 24 and voltage_from_name("GND") is None


# --- circuit map on the CAN board ---

@pytest.fixture
def cmap(design):
    return cm.build_circuit_map(design)


def test_rails_and_classes(cmap):
    rails = {r.name: r.voltage for r in cmap.rails}
    assert rails["+24V"] == 24 and rails["+5V"] == 5 and rails["+3.3V"] == 3.3
    assert cmap.nets["GND"].net_class == "ground"
    assert cmap.nets["CANH"].net_class == "signal"


def test_feedback_divider_detected(cmap):
    div = next(s for s in cmap.structures if s.kind == "divider")
    assert div.refs == ["R1", "R2"] and "U1.1(FB)" in div.summary
    # 0.8 V reference × (1 + 100k/19.1k) ≈ 4.99 V: the divider reads ~0.80 V at the tap off +5V.
    assert div.detail["v_tap"] == pytest.approx(0.802, abs=0.002)


def test_pullups_and_supply_caps(cmap):
    kinds = {(s.kind, tuple(s.refs)) for s in cmap.structures}
    assert ("pull_up", ("R4",)) in kinds and ("pull_up", ("R5",)) in kinds
    assert ("pull_down", ("R6",)) in kinds
    u3_vcc = next(s for s in cmap.structures if s.kind == "supply_caps" and s.refs == ["U3"]
                  and "VCC" in s.summary)
    assert "+5V" in u3_vcc.nets and "C15 100n" in u3_vcc.summary


def test_pin_function_strips_kicad_suffix(cmap):
    u1 = cmap.parts["U1"]
    assert {p.function for p in u1.pins} >= {"EN", "VIN", "FB", "SW"}


def test_between_uses_signal_nets_only(cmap):
    assert "ESP_CAN_TX" in cm.between(cmap, "U3", "U4")
    assert "R8" in cm.between(cmap, "CANH", "Net-(J7-Pin_1)")


# --- evidence gate pieces ---

def test_check_claims(design):
    en_net = design.net_of_pin("U1", "2").name
    assert ra.check_claims(design, [ra.NetlistClaim(ref="U1", pin="EN", net=en_net)]) == []
    bad = ra.check_claims(design, [ra.NetlistClaim(ref="U1", pin="EN", net="GND")])
    assert bad and f"on net {en_net}" in bad[0]
    assert "no pin" in ra.check_claims(design, [ra.NetlistClaim(ref="U1", pin="ZZ", net="x")])[0]


def test_quote_on_page_tolerates_extraction_spacing():
    page = "The VCC pin requires a 100 nF decoupling capacitor placed close to the device."
    assert ra.quote_on_page("requires a 100 nF decoupling capacitor", page)
    assert ra.quote_on_page("requires a 100nF decoupling   capacitor", page)
    assert not ra.quote_on_page("requires a 10 uF bulk capacitor on VIO", page)


def test_safe_eval():
    assert ra.safe_eval("0.8*(1+100k/19.1k)") == pytest.approx(4.988, abs=0.001)
    with pytest.raises(ValueError):
        ra.safe_eval("__import__('os')")


# --- the reviewer, scripted ---

def _scripted(steps):
    """A FunctionModel that plays `steps` in order: each is a list of tool calls
    (name, args) or a final text."""
    import json

    from pydantic_ai.models.function import DeltaToolCall

    def pick(messages):
        turn = sum(1 for m in messages if isinstance(m, ModelResponse))
        return turn, steps[min(turn, len(steps) - 1)]

    def fn(messages, info):
        turn, step = pick(messages)
        if isinstance(step, str):
            return ModelResponse(parts=[TextPart(step)])
        return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=f"c{turn}{i}")
                                    for i, (name, args) in enumerate(step)])

    async def stream(messages, info):  # run_reviewer streams (for live steps)
        turn, step = pick(messages)
        if isinstance(step, str):
            yield step
            return
        yield {i: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"c{turn}{i}")
               for i, (name, args) in enumerate(step)}

    return FunctionModel(fn, stream_function=stream)


def _tool_returns(result):
    return [p.content for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]


def test_reviewer_gate_refuses_then_records_with_evidence(design, tmp_path, monkeypatch):
    lib = DatasheetLibrary.for_project(tmp_path)
    lib.add_upload(part_for(design, "U3"), make_pdf([
        "TJA1051 high-speed CAN transceiver",
        "Pin S selects Silent mode. Connect S to GND for Normal mode.",
    ]))
    s_net = design.net_of_pin("U3", "8").name
    claim = {"ref": "U3", "pin": "S", "net": s_net}
    finding = {
        "severity": "verify", "title": "Check S pin mode", "detail": "S tied low = Normal mode",
        "failure_mode": "silent mode would block TX", "component_ref": "U3",
        "netlist_claims": [claim], "datasheet_part": "U3", "page": 2,
        "quote": "Connect S to GND for Normal mode",
    }
    steps = [
        [("report_finding", {**finding, "netlist_claims": [{**claim, "net": "+5V"}]})],  # wrong net
        [("report_finding", finding)],  # page not read yet
        [("read_datasheet_pages", {"part": "U3", "pages": [2]})],
        [("report_finding", {**finding, "quote": "S must float"})],  # quote not on page
        [("report_finding", finding)],
        "Checked U3 pins against the datasheet.",
    ]
    monkeypatch.setattr("pcb_assistant.agent.agent.to_pai_model", lambda spec: _scripted(steps))
    monkeypatch.setattr("pcb_assistant.agent.agent.thinking_settings", lambda spec: {})

    sheets = ra.DatasheetTools(tmp_path, design)
    run = ra.ReviewerRun(name="signals")
    events = []
    agent = ra.build_reviewer("x:y", design, cm.build_circuit_map(design), sheets, run,
                              "CAN transceiver U3", events.append)
    result = agent.run_sync("review")

    returns = _tool_returns(result)
    assert "netlist says otherwise" in returns[0] and f"is on net {s_net}" in returns[0]
    assert "haven't read" in returns[1]
    assert "quote isn't on" in returns[3]
    assert returns[4].startswith("Recorded with evidence TJA1051TK-3 datasheet p.2")
    (f,) = run.findings
    assert f.page == 2 and f.datasheet == "TJA1051TK-3" and f.connectivity_verified == "verified"
    assert f.citation == "TJA1051TK-3 datasheet p.2"
    assert any(e["status"] == "finding" for e in events)


def test_run_reviewer_reports_steps_and_runs_out_gracefully(design, tmp_path, monkeypatch):
    # A model that never stops calling tools hits the request limit; the review
    # still returns, with an abstention saying it was cut short.
    monkeypatch.setattr(ra, "REQUEST_LIMIT", 3)
    monkeypatch.setattr("pcb_assistant.agent.agent.to_pai_model",
                        lambda spec: _scripted([[("rails", {})]]))
    monkeypatch.setattr("pcb_assistant.agent.agent.thinking_settings", lambda spec: {})
    events = []
    found = ra.run_reviewer(design, "x:y", "can", "CAN block", tmp_path, events.append,
                            refs=["U3", "U5"])
    assert [f.title for f in found] == [
        "Audit of U3 ran out of steps before finishing",
        "Audit of U5 ran out of steps before finishing",
    ]
    steps = [e["summary"] for e in events if e["status"] == "step"]
    assert steps[0].startswith("auditing U3") and "U3: listing power rails" in steps
    assert any(s.startswith("auditing U5") for s in steps)
    usage = [e for e in events if e["status"] == "usage"]
    assert usage and usage[0]["requests"] == 6  # 3 per audit, summed


def test_scopes_split_power_and_signals(design):
    from pcb_assistant.reasoning.review_team import scope_refs

    assert scope_refs(design, "power", None) == ["U1", "U2", "CR1"]
    assert scope_refs(design, "signals", None) == ["U3", "U4", "U5", "D3", "D4"]
    assert scope_refs(design, "board", "the TJA1051 CAN transceiver") == ["U3"]
    assert scope_refs(design, "leds", "status LED Parts: D1, R3") == []


# --- board understanding ---

def test_understanding_cached_per_netlist(design, tmp_path, monkeypatch):
    import json as _json

    from pcb_assistant.analysis import understanding as und

    calls = []
    reply = {
        "summary": "A CAN node.",
        "blocks": [{"name": "Buck 24V to 5V", "purpose": "makes 5V", "refs": ["U1", "L1"]},
                   {"name": "CAN", "purpose": "bus interface", "refs": ["U3"]}],
        "parts": {"U1": "24V to 5V buck"},
        "nets": {"CANH": "CAN bus high line"},
    }
    monkeypatch.setattr("pcb_assistant.providers.ask",
                        lambda *a, **k: calls.append(1) or _json.dumps(reply))
    u = und.understand(tmp_path, design, "x:y")
    assert u.parts["U1"] == "24V to 5V buck"
    und.understand(tmp_path, design, "x:y")
    assert len(calls) == 1  # cached

    cmap = cm.build_circuit_map(design)
    assert "Purpose: 24V to 5V buck" in und.explain_part(cmap, u, "U1")
    assert und.explain_net(cmap, u, "CANH").startswith("Why it exists: CAN bus high line")

    from pcb_assistant.reasoning.review_team import reviewers_for
    team = reviewers_for(design, "x:y", "high", tmp_path)
    assert [n for n, _ in team] == ["buck-24v-to-5v", "can"] and "U1, L1" in team[0][1]


def test_compact_history_trims_old_pages_keeps_recent(monkeypatch):
    from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart, UserPromptPart

    monkeypatch.setattr(ra, "COMPACT_AT_CHARS", 5_000)
    big = "page text " * 300
    msgs = [ModelRequest(parts=[UserPromptPart("review")])]
    for i in range(8):
        msgs.append(ModelRequest(parts=[
            ToolReturnPart("read_datasheet_pages", big, tool_call_id=f"t{i}"),
            UserPromptPart(["pages", BinaryContent(data=b"%PDF-x", media_type="application/pdf")]),
        ]))
    out = ra.compact_history(msgs)
    assert out[0] is msgs[0]  # the task itself is never trimmed
    assert out[1].parts[0].content.endswith(ra._TRIMMED)
    assert isinstance(out[1].parts[1].content[1], str)
    assert out[-1].parts[0].content == big  # recent requests intact
    assert isinstance(out[-1].parts[1].content[1], BinaryContent)
    # below the threshold nothing changes
    monkeypatch.setattr(ra, "COMPACT_AT_CHARS", 10**9)
    assert ra.compact_history(msgs) is msgs


@pytest.mark.parametrize("verdict,expect", [("confirmed", "must-fix"), ("rejected", "verify"), ("unsure", "verify")])
def test_must_fix_needs_ruled_out_and_a_second_opinion(design, tmp_path, monkeypatch, verdict, expect):
    import json as _json

    lib = DatasheetLibrary.for_project(tmp_path)
    lib.add_upload(part_for(design, "U5"), make_pdf(["74AHCT1G125 bus buffer", "OE low enables the output"]))
    oe_net = design.net_of_pin("U5", "1").name
    finding = {
        "severity": "must-fix", "title": "U5 output never enabled", "detail": "OE tied low",
        "failure_mode": "no data to the LED", "component_ref": "U5",
        "netlist_claims": [{"ref": "U5", "pin": "1", "net": oe_net}],
        "datasheet_part": "U5", "page": 2, "quote": "OE low enables the output",
    }
    steps = [
        [("read_datasheet_pages", {"part": "U5", "pages": [2]})],
        [("report_finding", finding)],  # no ruled_out
        [("report_finding", {**finding, "ruled_out": "OE is not active-low on this part, checked p.2"})],
        "done",
    ]
    monkeypatch.setattr("pcb_assistant.agent.agent.to_pai_model", lambda spec: _scripted(steps))
    monkeypatch.setattr("pcb_assistant.agent.agent.thinking_settings", lambda spec: {})
    monkeypatch.setattr("pcb_assistant.providers.ask",
                        lambda *a, **k: _json.dumps({"verdict": verdict, "reason": "OE is active-low"}))

    sheets = ra.DatasheetTools(tmp_path, design)
    run = ra.ReviewerRun(name="signals")
    agent = ra.build_reviewer("x:y", design, cm.build_circuit_map(design), sheets, run, "U5")
    returns = _tool_returns(agent.run_sync("audit"))
    assert "needs ruled_out" in returns[1]
    (f,) = run.findings
    assert f.severity == expect
    assert ("disputed" in f.labels) is (expect == "verify")


def test_reviewer_settings_reduce_thinking_only_on_thinking_models():
    thinking = ra.reviewer_settings("claude:claude-sonnet-5-5")
    assert thinking["thinking"] == ra.REVIEWER_THINKING
    assert thinking["max_tokens"] == ra.REPLY_MAX_TOKENS
    plain = ra.reviewer_settings("claude:claude-haiku-4-5")
    assert "thinking" not in plain and plain["max_tokens"] == ra.REPLY_MAX_TOKENS
