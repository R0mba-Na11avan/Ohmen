"""Footprint generation: spec checks, IPC land patterns against KiCad's own
library, the .kicad_mod writer, stock matching, and the accept flow."""
import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from pcb_assistant.footprints import proposals, stock
from pcb_assistant.footprints.build import build
from pcb_assistant.footprints.kicad_mod import to_sexpr
from pcb_assistant.footprints.spec import FootprintSpec, check

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "footprints" / "kicad_stock_pads.json").read_text())
SPEC = TypeAdapter(FootprintSpec)
POS_TOL = 0.1  # mm: our IPC numbers vs KiCad's library
DRILL_TOL = 0.2  # we size holes from the lead's largest diagonal + 0.2 mm; KiCad's headers run tighter


def spec_of(raw):
    return SPEC.validate_python(raw)


def d(lo, hi, src="test p.1"):
    return {"min": lo, "max": hi, "source": src}


SOIC8 = GOLDEN["footprints"]["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"]["spec"]
QFN24 = GOLDEN["footprints"]["Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm"]["spec"]


@pytest.mark.parametrize("name", sorted(GOLDEN["footprints"]))
def test_land_patterns_match_kicads_library(name):
    case = GOLDEN["footprints"][name]
    spec = spec_of(case["spec"])
    assert check(spec)[0] == []
    fp, _ = build(spec, "X")
    ours = {p.number: p for p in fp.pads if p.number}
    assert len(ours) == len(case["pads"])
    for t in case["pads"]:
        o = ours[t["number"]]
        assert abs(o.x - t["x"]) <= POS_TOL and abs(o.y - t["y"]) <= POS_TOL, (name, t["number"])
        assert abs(o.w - t["w"]) <= POS_TOL and abs(o.h - t["h"]) <= POS_TOL, (name, t["number"])
        if t["drill"] is not None:
            assert abs(o.drill - t["drill"]) <= DRILL_TOL


def test_sot23_numbering_skips_positions():
    fp, _ = build(spec_of(GOLDEN["footprints"]["Package_TO_SOT_SMD:SOT-23"]["spec"]), "X")
    pads = {p.number: (p.x, p.y) for p in fp.pads}
    assert pads["1"][0] < 0 and pads["2"][0] < 0 and pads["3"] == (pytest.approx(pads["3"][0]), 0)
    assert pads["1"][1] < 0 < pads["2"][1] and pads["3"][0] > 0


def test_exposed_pad_is_numbered_last_and_paste_is_split():
    fp, warnings = build(spec_of(QFN24), "X")
    ep = [p for p in fp.pads if p.number == "25"]
    paste = [p for p in fp.pads if not p.number]
    assert len(ep) == 1 and "F.Paste" not in ep[0].layers
    assert len(paste) >= 4 and all(p.layers == ("F.Paste",) for p in paste)
    covered = sum(p.w * p.h for p in paste) / (ep[0].w * ep[0].h)
    assert 0.55 < covered < 0.75


@pytest.mark.parametrize("name", sorted(GOLDEN["footprints"]))
def test_silkscreen_stays_off_copper_and_courtyard_encloses_it(name):
    fp, _ = build(spec_of(GOLDEN["footprints"][name]["spec"]), "X")
    copper = [p for p in fp.pads if p.number]
    for ln in [x for x in fp.lines if x.layer == "F.SilkS"]:
        x0, x1 = sorted((ln.x1, ln.x2))
        y0, y1 = sorted((ln.y1, ln.y2))
        for p in copper:
            b = p.bbox
            assert x1 < b[0] - 0.2 or x0 > b[2] + 0.2 or y1 < b[1] - 0.2 or y0 > b[3] + 0.2, (name, p.number)
    crt = [x for x in fp.lines if x.layer == "F.CrtYd"]
    cx = [v for ln in crt for v in (ln.x1, ln.x2)]
    cy = [v for ln in crt for v in (ln.y1, ln.y2)]
    for p in copper:
        assert min(cx) <= p.bbox[0] - 0.25 + 1e-9 and max(cx) >= p.bbox[2] + 0.25 - 1e-9
        assert min(cy) <= p.bbox[1] - 0.25 + 1e-9 and max(cy) >= p.bbox[3] + 0.25 - 1e-9


@pytest.mark.parametrize("change,needle", [
    ({"pitch": 2.0}, "longer than the body"),
    ({"lead_span": d(3.0, 3.5)}, "wider than the body"),
    ({"positions": 7}, "must be even"),
    ({"skip_positions": [9]}, "within 1..8"),
    ({"lead_width": d(0.31, 1.4)}, "doesn't fit"),
    ({"lead_length": d(0.4, 4.0)}, "feet longer"),
    ({"body_width": {"min": 4.0, "max": 3.8, "source": "p.1"}}, "min ≤ nom ≤ max"),
])
def test_check_catches_numbers_that_cant_be_a_package(change, needle):
    errors, _ = check(spec_of({**SOIC8, **change}))
    assert any(needle in e for e in errors), errors


def test_check_warns_about_missing_sources_and_tolerances():
    raw = {**SOIC8, "pitch": 1.27, "lead_width": {"nom": 0.4}}
    errors, warnings = check(spec_of(raw))
    assert errors == []
    assert any("nominal only" in w for w in warnings) and any("no datasheet page" in w for w in warnings)


def test_custom_pads_must_not_overlap():
    raw = {"family": "custom", "package": "odd", "source": "p.9", "body_x": 3, "body_y": 3,
           "pads": [{"number": "1", "x": 0, "y": 0, "width": 1, "height": 1},
                    {"number": "2", "x": 0.5, "y": 0, "width": 1, "height": 1}]}
    assert any("overlap" in e for e in check(spec_of(raw))[0])


def test_kicad_mod_is_well_formed():
    fp, _ = build(spec_of(QFN24), "TPS62A01")
    text = to_sexpr(fp)
    depth = 0
    for ch in re.sub(r'"(?:\\.|[^"\\])*"', '""', text):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        assert depth >= 0
    assert depth == 0
    assert text.startswith('(footprint "QFN-24_TPS62A01"') and "(version 20241229)" in text
    assert len(re.findall(r'\(pad "\d+"', text)) == 25 and '(pad "" smd roundrect' in text  # 24 + EP; paste windows
    assert "(attr smd)" in text and "F.CrtYd" in text and "${REFERENCE}" in text


def test_kicad_cli_reads_the_file():
    try:
        svg = proposals.render_svg(to_sexpr(build(spec_of(SOIC8), "X")[0]), "SOIC-8_X")
    except FileNotFoundError:
        pytest.skip("no kicad-cli here")
    assert "<svg" in svg


def test_preview_leaves_out_the_reference_and_value_text():
    text = to_sexpr(build(spec_of(SOIC8), "A_VERY_LONG_PART_NUMBER")[0])
    stripped = proposals._FIELD_TEXT.sub("", text)
    assert '(property "Reference"' in text and '(property "Value"' in text
    assert "(property" not in stripped and stripped.count("(pad ") == text.count("(pad ")
    assert '(fp_text user "${REFERENCE}"' in stripped  # the fab label inside the body stays


def test_name_doesnt_repeat_a_package_the_part_number_starts_with():
    raw = {"family": "custom", "package": "DFE252012F (2520 / 1008)", "source": "p.1", "body_x": 2.5,
           "body_y": 2.0, "pads": [{"number": "1", "x": -1, "y": 0, "width": 0.8, "height": 2},
                                   {"number": "2", "x": 1, "y": 0, "width": 0.8, "height": 2}]}
    assert build(spec_of(raw), "DFE252012F-4R7M=P2")[0].name == "DFE252012F-4R7M_P2"
    assert build(spec_of(SOIC8), "TPS54202")[0].name == "SOIC-8_TPS54202"


def test_stock_matching_by_name(tmp_path):
    for lib, names in {
        "Package_SO": ["SOIC-8_3.9x4.9mm_P1.27mm", "SO-8_3.9x4.9mm_P1.27mm", "SOIC-8_5.3x5.3mm_P1.27mm",
                       "TSSOP-8_4.4x3mm_P0.65mm"],
        "Package_DFN_QFN": ["QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm", "QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm_ThermalVias",
                            "QFN-24-1EP_4x4mm_P0.5mm_EP2.1x2.1mm", "QFN-24_4x4mm_P0.5mm"],
        "Resistor_SMD": ["R_0603_1608Metric", "R_0603_1608Metric_Pad0.98x0.95mm_HandSolder"],
    }.items():
        (tmp_path / f"{lib}.pretty").mkdir()
        for n in names:
            (tmp_path / f"{lib}.pretty" / f"{n}.kicad_mod").write_text("")
    assert stock.matches(spec_of(SOIC8), tmp_path)[:2] == ["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                                                           "Package_SO:SO-8_3.9x4.9mm_P1.27mm"]
    assert stock.matches(spec_of(QFN24), tmp_path) == [
        "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm",
        "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm_ThermalVias"]
    r0603 = GOLDEN["footprints"]["Resistor_SMD:R_0603_1608Metric"]["spec"]
    assert stock.matches(spec_of(r0603), tmp_path) == ["Resistor_SMD:R_0603_1608Metric"]
    assert stock.matches(spec_of({**QFN24, "pins": 32, "pitch": 0.4}), tmp_path) == []


def _proposal(project, part="U9"):
    spec = spec_of(SOIC8)
    fp, warnings = build(spec, part)
    return proposals.propose(project, part, spec, fp, to_sexpr(fp), warnings, [])


def test_accept_writes_the_library_and_registers_it(tmp_path):
    p = _proposal(tmp_path)
    assert not (tmp_path / "ohmen.pretty").exists()  # proposing writes nothing into the project
    assert [x["number"] for x in p.pads] == [str(i) for i in range(1, 9)] and p.pads[0]["w"] > 0
    done = proposals.FootprintStore(tmp_path).accept(p.id)
    assert done.status == "accepted" and done.file == "ohmen.pretty/SOIC-8_U9.kicad_mod"
    assert (tmp_path / done.file).read_text().startswith('(footprint "SOIC-8_U9"')
    table = (tmp_path / "fp-lib-table").read_text()
    assert '(name "ohmen")' in table and "${KIPRJMOD}/ohmen.pretty" in table
    with pytest.raises(ValueError):
        proposals.FootprintStore(tmp_path).accept(p.id)


def test_accept_adds_to_an_existing_table_and_backs_it_up(tmp_path):
    original = '(fp_lib_table\n\t(version 7)\n\t(lib (name "mine")(type "KiCad")(uri "${KIPRJMOD}/mine.pretty")(options "")(descr ""))\n)\n'
    (tmp_path / "fp-lib-table").write_text(original)
    proposals.FootprintStore(tmp_path).accept(_proposal(tmp_path).id)
    table = (tmp_path / "fp-lib-table").read_text()
    assert table.count("(lib ") == 2 and '(name "mine")' in table and table.rstrip().endswith(")")
    assert (tmp_path / proposals.TABLE_BACKUP).read_text() == original
    proposals.FootprintStore(tmp_path).accept(_proposal(tmp_path, "U10").id)
    assert (tmp_path / "fp-lib-table").read_text().count('(name "ohmen")') == 1


def test_reject_writes_nothing(tmp_path):
    p = _proposal(tmp_path)
    assert proposals.FootprintStore(tmp_path).reject(p.id).status == "rejected"
    assert not (tmp_path / "ohmen.pretty").exists() and not (tmp_path / "fp-lib-table").exists()


def test_create_footprint_tool_asks_first_then_proposes(tmp_path, design, monkeypatch):
    from pcb_assistant.agent.agent import build_toolset
    from pcb_assistant.agent.stream import follow_up_frames

    monkeypatch.setattr(stock, "matches", lambda spec, lib=None: ["Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"])
    tool = build_toolset("test", tmp_path, design).tools["create_footprint"].function
    first = tool(part="U9", spec=SOIC8)
    assert "Ask the engineer" in first and "SOIC-8_3.9x4.9mm_P1.27mm" in first
    assert not (tmp_path / ".pcb_assistant" / "footprint_proposals.json").exists()
    assert "re-read the drawing" in tool(part="U9", spec={**SOIC8, "pitch": 2.0})
    assert "doesn't fit the format" in tool(part="U9", spec={"family": "soic"})
    out = tool(part="U9", spec=SOIC8, propose=True)
    pid = re.match(r"Footprint proposal \[(\w+)\]", out).group(1)
    assert proposals.FootprintStore(tmp_path).get(pid).status == "pending"
    assert follow_up_frames(tmp_path, "create_footprint", out) == [{"type": "footprint", "id": pid}]
