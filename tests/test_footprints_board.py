"""Footprints that change the board: cutouts milled through it and keepout
areas. Nothing here is repaired; a doubt goes back to the model."""
import re

import pytest

from pcb_assistant.footprints import proposals, stock
from pcb_assistant.footprints.build import build
from pcb_assistant.footprints.kicad_mod import to_sexpr
from pcb_assistant.footprints.spec import Refused
from pcb_assistant.footprints.svg import drawing
from tests.test_footprints import spec_of
from tests.test_footprints_custom import READ, USB_C
from tests.test_footprints_silk import MODULE

HOLE = {"points": [[-3.5, 0.2], [3.5, 0.2], [3.5, 4.4], [-3.5, 4.4]], "source": "attached drawing"}
NOTCH = {"points": [[-3.5, 4.4], [-3.5, 0.2], [3.5, 0.2], [3.5, 4.4]], "closed": False, "source": "attached drawing"}
ANTENNA = {"points": [[-5, -11], [5, -11], [5, -5.2], [-5, -5.2]], "source": "p.27"}  # beyond a module's top edge


def edge(fp):
    return [ln for ln in fp.lines if ln.layer == "Edge.Cuts"]


def test_a_mid_mount_connector_cuts_the_board():
    fp, _ = build(spec_of({**USB_C, "cutouts": [HOLE]}), "J1")
    assert len(edge(fp)) == 4 and (edge(fp)[-1].x2, edge(fp)[-1].y2) == (-3.5, 0.2)  # closed back to the start
    notch, _ = build(spec_of({**USB_C, "cutouts": [NOTCH]}), "J1")
    assert len(edge(notch)) == 3  # an open run: the board's own outline closes it
    crt = [ln for ln in fp.lines if ln.layer == "F.CrtYd"]
    assert max(ln.y1 for ln in crt) >= 4.65  # the courtyard takes the opening in
    text = to_sexpr(fp)
    assert text.count('(layer "Edge.Cuts")') == 4
    got = proposals.kicad_check(text, fp.name, proposals.pad_table(fp))
    assert got["status"] in READ and (got["status"] != "match" or "4 board cutout lines" in got["detail"])


def test_a_module_keeps_copper_out_from_under_its_antenna():
    fp, _ = build(spec_of({**MODULE, "keepouts": [ANTENNA, {**ANTENNA, "layers": "front", "vias": False}]}), "U1")
    everything, front = fp.zones
    assert everything.layers == ("*.Cu",) and (everything.tracks, everything.vias, everything.pour) == (True,) * 3
    assert front.layers == ("F.Cu",) and not front.vias
    text = to_sexpr(fp)
    assert text.count("(zone") == 2 and "(vias allowed)" in text and text.count("(tracks not_allowed)") == 2
    got = proposals.kicad_check(text, fp.name, proposals.pad_table(fp))
    assert got["status"] in READ and (got["status"] != "match" or "2 keepout areas" in got["detail"])
    svg = drawing(fp)  # Ohmen's drawing shows the area, well above the pads
    assert svg.count('stroke-dasharray="0.4 0.25"') == 2
    top = float(re.search(r'viewBox="[-\d.]+ ([-\d.]+) ', svg).group(1))
    assert top < -11


@pytest.mark.parametrize("extra, why", [
    ({"cutouts": [{**HOLE, "source": " "}]}, "never drawn from memory"),
    ({"cutouts": [{"points": [[0, 0], [1, 0]], "source": "p.1"}]}, "three points"),
    ({"cutouts": [{"points": [[0, 0]], "closed": False, "source": "p.1"}]}, "two points"),
    ({"cutouts": [{"points": [[-4, -4], [4, -4], [4, -2], [-4, -2]], "source": "p.1"}]}, "cuts through pad"),
    ({"cutouts": [{"points": [[-6, -3.05], [6, -3.05]], "closed": False, "source": "p.1"}]}, "cuts through pad"),
    ({"keepouts": [{**ANTENNA, "source": ""}]}, "where it is asked for"),
    ({"keepouts": [{"points": [[0, 0], [1, 0], [2, 0]], "source": "p.1"}]}, "enclose an area"),
    ({"keepouts": [{**ANTENNA, "tracks": False, "vias": False, "copper_pour": False}]}, "keeps nothing out"),
    ({"keepouts": [{"points": [[-5, -5], [5, -5], [5, 0], [-5, 0]], "source": "p.1"}]}, "covers pad"),
])
def test_doubtful_cutouts_and_keepouts_are_refused(extra, why):
    with pytest.raises(Refused) as e:
        build(spec_of({**USB_C, **extra}), "J1")
    assert why in " ".join(e.value.errors)


def test_a_keepout_may_cover_a_pad_it_doesnt_bar_tracks_from():
    over_pads = {"points": [[-5, -5], [5, -5], [5, 0], [-5, 0]], "source": "p.1"}
    fp, _ = build(spec_of({**USB_C, "pads": [], "holes": [],
                           "keepouts": [{**over_pads, "tracks": False}, {**over_pads, "layers": "back"}]}), "J1")
    assert len(fp.zones) == 2  # pour-only, and back-side only under SMD pads


def test_the_card_gets_risks_drawn_marks_and_repairs(tmp_path, design, monkeypatch):
    from pcb_assistant.agent.agent import build_toolset

    monkeypatch.setattr(stock, "matches", lambda spec, lib=None: [])
    spec = {**MODULE, "keepouts": [ANTENNA], "cutouts": [{**NOTCH, "points": [[-2, 12], [-2, 10], [2, 10], [2, 12]]}],
            "labels": {"text": {"1": "GND"}},
            "graphics": [{"type": "line", "start": [-7, 0], "end": [7, 0]},
                         {"type": "text", "at": [0, -2], "text": "ANT"}]}
    out = build_toolset("test", tmp_path, design).tools["create_footprint"].function(
        part="U1", spec=spec, propose=True)
    assert "Shown in red on the card: Cuts the board: an open notch" in out
    assert "Repaired: line 1: trimmed where it ran over a pad" in out
    (p,) = proposals.FootprintStore(tmp_path).list()
    assert p.risks == [
        "Cuts the board: an open notch (its ends must meet your board outline), 4.0 x 2.0 mm. From: attached drawing",
        "Keepout: no tracks, vias, copper pour on all copper layers, 10.0 x 5.8 mm. From: p.27"]
    assert p.fixes == ["line 1: trimmed where it ran over a pad"]
    assert p.drawn == [
        {"layer": "F.SilkS", "type": "line", "detail": "(-7.0, 0.0) to (7.0, 0.0)"},
        {"layer": "F.SilkS", "type": "text", "detail": "'ANT' at (0.0, -2.0), 1.0 mm"},
        {"layer": "F.SilkS", "type": "label", "detail": "pad 1: 'GND'"}]
    plain = proposals.propose(tmp_path, "U2", spec_of(MODULE), *_built(MODULE), [], [])
    assert plain.risks == [] and plain.drawn == [] and plain.fixes == []


def test_a_radio_module_numbered_and_labelled_from_its_symbol(tmp_path, design, monkeypatch):
    """The whole path for an ESP32-WROOM-32E class module: 38 castellated pads
    in rows on three sides and a ground pad underneath, numbered after a symbol
    in the project, labelled with its pin names, antenna area kept clear."""
    from pcb_assistant.agent.agent import build_toolset

    monkeypatch.setattr(stock, "matches", lambda spec, lib=None: [])
    names = {str(n): f"IO{n}" for n in range(1, 39)} | {"1": "GND", "2": "3V3", "3": "EN", "39": "GND"}
    pins = "".join(f'(pin bidirectional line (at 0 0 0) (length 2) (name "{name}") (number "{n}"))'
                   for n, name in names.items())
    (tmp_path / "radio.kicad_sym").write_text(
        f'(kicad_symbol_lib (version 20241209) (symbol "WROOM" (symbol "WROOM_1_1" {pins})))')
    side = {"pitch": 1.27, "width": 1.5, "height": 0.9}
    spec = {
        "family": "custom", "package": "ESP32-WROOM-32E", "drawing": "recommended land pattern, p.26",
        "source": "ESP32-WROOM-32E datasheet p.26", "symbol": "radio:WROOM", "body_x": 18.0, "body_y": 25.5,
        "rows": [
            {**side, "x": -8.75, "y": -5.26, "count": 14, "direction": "down"},
            {"pitch": 1.27, "width": 0.9, "height": 1.5, "x": -5.715, "y": 12.5, "count": 10, "start": 15,
             "direction": "right"},
            {**side, "x": 8.75, "y": 11.25, "count": 14, "start": 25, "direction": "up"},
        ],
        "pads": [{"number": "39", "x": -1.0, "y": 2.0, "width": 4.0, "height": 4.0, "shape": "rect"}],
        "keepouts": [{"points": [[-9, -12.75], [9, -12.75], [9, -6.5], [-9, -6.5]],
                      "source": "ESP32-WROOM-32E datasheet p.27"}],
        "labels": {"from_symbol": True},
    }
    tools = build_toolset("test", tmp_path, design).tools
    assert tools["get_symbol_pins"].function("radio:WROOM").startswith("symbol radio:WROOM: 39 pins")
    out = tools["create_footprint"].function(part="U1", spec=spec, propose=True)
    assert out.startswith("Footprint proposal ["), out
    (p,) = proposals.FootprintStore(tmp_path).list()
    assert p.symbol_check == {"status": "match", "detail": "All 39 pins of symbol radio:WROOM have a pad."}
    assert len(p.pads) == 39 and p.pads[2]["name"] == "EN" and p.pads[14]["name"] == "IO15"
    assert p.kicad_check["status"] in READ
    assert len(p.risks) == 1 and p.risks[0].startswith("Keepout: no tracks, vias, copper pour")
    # every edge pad carries its pin name; the ground pad in the middle has no room for one
    assert p.kicad_mod.count("(fp_text user") == 38 + 1
    assert p.fixes == ["label 'GND' left out: pad 39 isn't at the body's edge"]
    assert '(fp_text user "IO15"' in p.kicad_mod and "(zone" in p.kicad_mod
    again = tools["get_footprint_spec"].function(p.name)  # and it can be read back to revise
    assert '"count": 14' in again and '"from_symbol": true' in again and '"silk"' not in again


def _built(raw):
    fp, _ = build(spec_of(raw), "U2")
    return fp, to_sexpr(fp)
