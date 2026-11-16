"""Footprints beyond the standard packages: connectors and modules (pad rows,
slots, bare holes, turned pads), symbols, and the frozen output of old specs."""
import json
import re
from pathlib import Path

import pytest

from pcb_assistant.footprints import proposals, stock
from pcb_assistant.footprints.build import build
from pcb_assistant.footprints.kicad_mod import to_sexpr
from pcb_assistant.footprints.spec import check
from tests.test_footprints import GOLDEN, spec_of

FROZEN = json.loads((Path(__file__).parent / "fixtures" / "footprints" / "golden_kicad_mod.json").read_text())
READ = ("match", "unavailable")  # KiCad read the file back, where it's installed


@pytest.mark.parametrize("name", sorted(FROZEN["kicad_mod"]))
def test_specs_without_new_fields_write_the_same_file(name):
    raw = FROZEN["specs"].get(name) or GOLDEN["footprints"][name]["spec"]
    assert to_sexpr(build(spec_of(raw), "X")[0]) == FROZEN["kicad_mod"][name]


# --- connectors and modules: rows, slots, bare holes, turned pads ---

USB_C = {  # HRO TYPE-C-31-M-12 class: 12 SMD contacts, four slotted shield tabs, two locating pegs
    "family": "custom", "package": "USB-C-16P", "source": "attached drawing", "body_x": 8.94, "body_y": 7.3,
    "body_offset": [0, 0.6],
    "rows": [
        {"x": -3.2, "y": -3.05, "pitch": 0.8, "direction": "right", "width": 0.6, "height": 1.3,
         "numbers": ["A1", "A4"]},
        {"x": -1.75, "y": -3.05, "pitch": 0.5, "direction": "right", "width": 0.3, "height": 1.3,
         "numbers": ["B8", "A5", "B7", "A6", "A7", "B6", "A8", "B5"]},
        {"x": 2.4, "y": -3.05, "pitch": 0.8, "direction": "right", "width": 0.6, "height": 1.3,
         "numbers": ["B4", "B1"]},
    ],
    "pads": [
        {"number": "S1", "x": x, "y": y, "width": 1.0, "height": h, "shape": "oval", "drill": [0.6, h - 0.5],
         "paste": False}
        for x, y, h in ((-4.32, -2.5, 2.1), (4.32, -2.5, 2.1), (-4.32, 1.68, 1.6), (4.32, 1.68, 1.6))
    ],
    "holes": [{"x": -2.89, "y": -2.0, "drill": 0.65}, {"x": 2.89, "y": -2.0, "drill": 0.65}],
}


def test_rows_expand_to_numbered_pads():
    spec = spec_of(USB_C)
    assert check(spec)[0] == []
    fp, _ = build(spec, "J1")
    by = {p.number: p for p in fp.pads if p.kind == "smd"}
    assert list(by) == ["A1", "A4", "B8", "A5", "B7", "A6", "A7", "B6", "A8", "B5", "B4", "B1"]
    assert by["A5"].x == pytest.approx(-1.25) and by["B5"].x == pytest.approx(1.75) and by["B1"].x == pytest.approx(3.2)
    counted = spec_of({**USB_C, "rows": [{"x": 0, "y": -3, "count": 3, "start": 2, "step": 2, "pitch": 1,
                                          "direction": "down", "width": 0.5, "height": 0.5}], "pads": [], "holes": []})
    assert [(p.number, p.y) for p in build(counted, "J1")[0].pads] == [("2", -3), ("4", -2), ("6", -1)]


def test_slots_bare_holes_and_paste_free_tabs_reach_the_file():
    fp, _ = build(spec_of(USB_C), "J1")
    text = to_sexpr(fp)
    assert fp.smd and "(attr smd)" in text  # twelve SMD contacts outnumber four through-hole tabs
    assert '(pad "S1" thru_hole oval' in text and "(drill oval 0.6 1.6)" in text
    assert text.count('(pad "" np_thru_hole circle') == 2
    holes = [p for p in fp.pads if p.kind == "np_thru_hole"]
    assert all(p.solid and not p.number for p in holes)
    crt = [ln for ln in fp.lines if ln.layer == "F.CrtYd"]
    assert min(ln.y1 for ln in crt) < -3.7 and max(ln.x1 for ln in crt) > 4.8  # around tabs, pads and holes
    for ln in (ln for ln in fp.lines if ln.layer == "F.SilkS"):  # silkscreen stays off the holes too
        for h in holes:
            assert not (h.bbox[0] < max(ln.x1, ln.x2) and min(ln.x1, ln.x2) < h.bbox[2]
                        and h.bbox[1] < max(ln.y1, ln.y2) and min(ln.y1, ln.y2) < h.bbox[3])
    table = proposals.pad_table(fp)
    assert len(table) == 18 and sum(p["hole"] for p in table) == 2
    assert proposals.kicad_check(text, fp.name, table)["status"] in READ


def test_turned_and_chamfered_pads():
    raw = {"family": "custom", "package": "X", "source": "p.1", "body_x": 4, "body_y": 4, "pads": [
        {"number": "1", "x": -1.5, "y": 0, "width": 2.0, "height": 0.6, "rotation": 90},
        {"number": "2", "x": 1.5, "y": 0, "width": 1.2, "height": 1.2, "shape": "chamfered_rect",
         "chamfer": ["top_left"], "mask": False}]}
    fp, _ = build(spec_of(raw), "X")
    one, two = fp.pads
    assert one.bbox == pytest.approx((-1.8, -1.0, -1.2, 1.0))  # the box follows the turn
    text = to_sexpr(fp)
    assert "(at -1.5 0 90)" in text and "(chamfer top_left)" in text and "(roundrect_rratio 0)" in text
    assert two.layers == ("F.Cu", "F.Paste")
    assert proposals.kicad_check(text, fp.name, proposals.pad_table(fp))["status"] in READ
    assert "which corners" in check(spec_of({**raw, "pads": [{**raw["pads"][1], "chamfer": []}]}))[0][0]


def test_custom_checks_catch_bad_rows_slots_and_holes():
    base = {"family": "custom", "package": "X", "source": "p.1", "body_x": 4, "body_y": 4}
    row = {"x": 0, "y": 0, "pitch": 1, "direction": "right", "width": 0.5, "height": 0.5}
    assert "give count or numbers" in check(spec_of({**base, "rows": [row]}))[0][0]
    assert "2 numbers are given" in check(spec_of({**base, "rows": [{**row, "count": 3, "numbers": ["1", "2"]}]}))[0][0]
    assert "overlap" in check(spec_of({**base, "rows": [{**row, "count": 2, "pitch": 0.4}]}))[0][0]
    slot = {"number": "1", "x": 0, "y": 0, "width": 1.0, "height": 2.0, "drill": [0.6, 2.0]}
    assert "doesn't leave copper" in check(spec_of({**base, "pads": [slot]}))[0][0]
    on_pad = {**base, "pads": [{**slot, "drill": None}], "holes": [{"x": 0.2, "y": 0, "drill": 0.5}]}
    assert "lands on pad 1" in check(spec_of(on_pad))[0][0]
    # same-numbered pads may overlap: an L-shaped land from two rectangles
    ell = [{"number": "1", "x": 0, "y": 0, "width": 2, "height": 0.5},
           {"number": "1", "x": -0.75, "y": 0.5, "width": 0.5, "height": 1.5}]
    assert check(spec_of({**base, "pads": ell}))[0] == []


# --- symbols: pins read from the project, pads checked against them ---

SYMBOL_LIB = """(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")
  (symbol "RADIO-MOD" (property "Reference" "U" (at 0 0 0))
    (symbol "RADIO-MOD_0_1" (rectangle (start -5 5) (end 5 -5)))
    (symbol "RADIO-MOD_1_1"
      (pin power_in line (at 0 7 270) (length 2) (name "3V3" (effects (font (size 1 1)))) (number "2" (effects (font (size 1 1)))))
      (pin power_in line (at 0 -7 90) (length 2) (name "GND" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))
      (pin bidirectional line (at 7 0 180) (length 2) (name "IO0" (effects (font (size 1 1)))) (number "10" (effects (font (size 1 1)))))
      (pin input line (at -7 0 0) (length 2) (name "~" (effects (font (size 1 1)))) (number "3" (effects (font (size 1 1)))))))
  (symbol "RADIO-MOD-B" (extends "RADIO-MOD") (property "Value" "B" (at 0 0 0))))
"""


def test_symbol_pins_come_from_the_project_library_or_the_schematic(tmp_path, design):
    from pcb_assistant.footprints import symbols

    (tmp_path / "mine.kicad_sym").write_text(SYMBOL_LIB)
    (tmp_path / "proj-backups").mkdir()
    (tmp_path / "proj-backups" / "old.kicad_sym").write_text(SYMBOL_LIB.replace("RADIO-MOD", "STALE"))
    lib = symbols.library(tmp_path)
    assert set(lib) == {"mine:RADIO-MOD", "mine:RADIO-MOD-B"}
    what, pins = symbols.find(tmp_path, None, "radio-mod-b")  # derived symbols take the parent's pins
    assert what == "symbol mine:RADIO-MOD-B"
    assert [(p.number, p.name, p.type) for p in pins] == [
        ("1", "GND", "power_in"), ("2", "3V3", "power_in"), ("3", "", "input"), ("10", "IO0", "bidirectional")]
    assert symbols.find(tmp_path, None, "nope") is None
    ref = design.nets[0].nodes[0].ref  # a placed part answers by refdes, from the netlist
    what, pins = symbols.find(tmp_path, design, ref)
    assert what == f"{ref} on the schematic" and pins


def test_pads_are_checked_against_the_symbol():
    from pcb_assistant.footprints.symbols import SymbolPin, cross_check

    sym = ("symbol mine:X", [SymbolPin(n, f"P{n}", "passive") for n in ("1", "2", "3")])
    assert cross_check(["1", "2", "3", "3"], sym)["status"] == "match"
    missing = cross_check(["1", "2", "MP"], sym)
    assert missing["status"] == "mismatch" and "pin with no pad: 3" in missing["detail"] and "MP" in missing["detail"]
    assert cross_check(["1", "2", "3", "MP"], sym)["status"] == "extra"  # a mounting pad the symbol leaves out


def test_a_footprint_follows_its_symbol(tmp_path, design, monkeypatch):
    from pcb_assistant.agent.agent import build_toolset

    monkeypatch.setattr(stock, "matches", lambda spec, lib=None: [])
    (tmp_path / "mine.kicad_sym").write_text(SYMBOL_LIB)
    tools = build_toolset("test", tmp_path, design).tools
    assert "mine:RADIO-MOD" in tools["get_symbol_pins"].function()
    assert "10\tIO0\tbidirectional" in tools["get_symbol_pins"].function("mine:RADIO-MOD")
    spec = {"family": "custom", "package": "MOD", "source": "engineer", "body_x": 6, "body_y": 6,
            "symbol": "mine:RADIO-MOD",
            "rows": [{"x": -3, "y": -1.5, "pitch": 1.5, "direction": "down", "width": 1.2, "height": 0.9,
                      "numbers": ["1", "2", "3"]}]}
    create = tools["create_footprint"].function
    assert "No symbol 'mine:NOPE'" in create(part="U1", spec={**spec, "symbol": "mine:NOPE"})
    out = create(part="U1", spec=spec, propose=True)
    assert "Against the symbol (mismatch)" in out and "pin with no pad: 10" in out
    spec["rows"][0]["numbers"] = ["1", "2", "3", "10"]
    out = create(part="U1", spec=spec, propose=True)
    p = proposals.FootprintStore(tmp_path).get(re.match(r"Footprint proposal \[(\w+)\]", out).group(1))
    assert p.symbol_check["status"] == "match"
    assert [(x["number"], x["name"]) for x in p.pads] == [("1", "GND"), ("2", "3V3"), ("3", ""), ("10", "IO0")]


def _two_pads(*pads, **extra):
    base = {"family": "custom", "package": "X", "drawing": "p.1", "source": "p.1", "body_x": 3.0, "body_y": 2.0}
    return spec_of({**base, "pads": list(pads), **extra})


def _pad(number, x, y, **more):
    return {"number": number, "x": x, "y": y, "width": 0.8, "height": 0.8, **more}


@pytest.mark.parametrize("spec, why", [
    # copper with no number would be left out of the courtyard, the silkscreen keep-off and the pad table
    (_two_pads(_pad("1", -1, 0), _pad("", 0, 1.6, width=2, height=1)), "give it a number"),
    # KiCad draws a circle pad at its width: the height would be a lie
    (_two_pads(_pad("1", -1, 0, shape="circle", height=2.5), _pad("2", 1, 0)), "one diameter"),
    # a bare hole through a pad, however the pad is turned
    (_two_pads(_pad("1", 0, 0, width=2, height=1, rotation=30), _pad("2", 3, 0),
               holes=[{"x": 0.7, "y": -0.4, "drill": 0.3}]), "lands on pad 1"),
])
def test_pads_that_would_fool_the_later_checks_are_refused(spec, why):
    errors, _ = check(spec)
    assert why in " ".join(errors)


def test_a_hole_beside_a_turned_pad_is_fine():
    spec = _two_pads(_pad("1", 0, 0, width=2, height=1, rotation=30), _pad("2", 3, 0),
                     holes=[{"x": 0.7, "y": 0.6, "drill": 0.3}])  # inside the pad's box, off its copper
    assert check(spec)[0] == []
