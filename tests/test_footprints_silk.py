"""Silkscreen the model controls: outline and polarity styles, its own
graphics held to the fab's rules, pad labels, and a polygon body."""
import pytest

from pcb_assistant.footprints import graphics, proposals, stock
from pcb_assistant.footprints.build import build
from pcb_assistant.footprints.kicad_mod import SILK_WIDTH, to_sexpr
from pcb_assistant.footprints.spec import Refused, check
from tests.test_footprints import SOIC8, spec_of
from tests.test_footprints_custom import READ, SYMBOL_LIB

SMA = {  # SMA diode: pad 1 is the cathode
    "family": "custom", "package": "SMA", "source": "p.4", "body_x": 4.3, "body_y": 2.6,
    "pads": [{"number": "1", "x": -2.0, "y": 0, "width": 1.5, "height": 1.7},
             {"number": "2", "x": 2.0, "y": 0, "width": 1.5, "height": 1.7}]}
MODULE = {  # castellated module, ESP32-WROOM class: rows down both long sides
    "family": "custom", "package": "MOD-12", "source": "p.3", "body_x": 10, "body_y": 10,
    "rows": [{"x": -5, "y": -3.175, "count": 6, "pitch": 1.27, "direction": "down", "width": 1.5, "height": 0.9},
             {"x": 5, "y": 3.175, "count": 6, "pitch": 1.27, "direction": "up", "width": 1.5, "height": 0.9,
              "start": 7}]}


def silk(fp):
    return [ln for ln in fp.lines if ln.layer == "F.SilkS"]


def off_copper(fp, width=SILK_WIDTH):
    """No silkscreen line runs inside a pad's clearance."""
    boxes = graphics.keepouts([p for p in fp.pads if p.solid], width)
    return not any(graphics._inside((ln.x1, ln.y1), (ln.x2, ln.y2), k) for ln in silk(fp) for k in boxes)


def test_outline_styles():
    box = silk(build(spec_of(SOIC8), "U1")[0])
    corners = silk(build(spec_of({**SOIC8, "silk": {"outline": "corners"}}), "U1")[0])
    assert sum(abs(ln.x2 - ln.x1) + abs(ln.y2 - ln.y1) for ln in corners) < sum(
        abs(ln.x2 - ln.x1) + abs(ln.y2 - ln.y1) for ln in box)
    assert corners and silk(build(spec_of({**SOIC8, "silk": {"outline": "none", "pin1": "none"}}), "U1")[0]) == []


@pytest.mark.parametrize("mark", ["dot", "line", "triangle", "none"])
def test_pin1_styles(mark):
    fp = build(spec_of({**SOIC8, "silk": {"pin1": mark}}), "U1")[0]
    plain = build(spec_of({**SOIC8, "silk": {"pin1": "none"}}), "U1")[0]
    assert len(fp.circles) == (mark == "dot") and len(fp.polys) == (mark == "triangle")
    assert len(silk(fp)) - len(silk(plain)) == (mark == "line")
    assert off_copper(fp)
    if mark == "dot":  # beside pad 1, off it
        p1 = next(p for p in fp.pads if p.number == "1")
        c = fp.circles[0]
        assert c.fill and (c.y + c.r < p1.bbox[1] or c.x + c.r < p1.bbox[0])


def test_polarity_marks_sit_at_pad_ones_end():
    plain = build(spec_of(SMA), "D1")[0]
    bar = build(spec_of({**SMA, "silk": {"polarity": "cathode_bar"}}), "D1")[0]
    extra = [ln for ln in silk(bar) if ln not in silk(plain)]
    assert len(extra) == 1 and extra[0].x1 == extra[0].x2  # one vertical bar...
    p1 = next(p for p in bar.pads if p.number == "1")
    assert extra[0].x1 < p1.bbox[0] and off_copper(bar)  # ...beyond the cathode pad
    assert bar.polys == []  # the bar is the polarity mark: no pin-1 triangle as well
    plus = build(spec_of({**SMA, "silk": {"polarity": "plus"}}), "C1")[0]
    arms = [ln for ln in silk(plus) if ln not in silk(plain)]
    assert len(arms) == 2 and all(max(ln.x1, ln.x2) < p1.bbox[0] for ln in arms) and off_copper(plus)
    flipped = {**SMA, "pads": [{**SMA["pads"][0], "x": 2.0}, {**SMA["pads"][1], "x": -2.0}],
               "silk": {"polarity": "cathode_bar"}}
    right = [ln for ln in silk(build(spec_of(flipped), "D1")[0]) if ln.x1 == ln.x2 and ln.x1 > 2.75]
    assert len(right) == 1


def test_own_graphics_reach_the_file_and_kicad_reads_it():
    raw = {**MODULE, "graphics": [
        {"type": "arc", "start": [-2, 0], "mid": [0, -2], "end": [2, 0]},
        {"type": "circle", "center": [0, 2], "radius": 1.0},
        {"type": "polygon", "points": [[-1, -4], [1, -4], [0, -3]], "fill": True},
        {"type": "text", "at": [0, -1], "text": "ANT"},
        {"type": "text", "at": [0, 4], "text": "keep clear", "layer": "Cmts.User", "size": 0.5},
        {"type": "line", "start": [-3, 4.5], "end": [3, 4.5], "layer": "F.Fab"},
    ]}
    fp, _ = build(spec_of(raw), "U1")
    assert fp.fixes == [] and len(fp.arcs) == 1 and len(fp.circles) == 1 and len(fp.texts) == 2
    text = to_sexpr(fp)
    for token in ("(fp_arc", "(mid 0 -2)", "(fp_circle", '(fp_text user "ANT"', '(layer "Cmts.User")'):
        assert token in text
    assert proposals.kicad_check(text, fp.name, proposals.pad_table(fp))["status"] in READ


def test_silkscreen_over_a_pad_is_trimmed_and_noted():
    raw = {**MODULE, "graphics": [
        {"type": "line", "start": [-7, 0], "end": [7, 0]},  # straight through both pad rows
        {"type": "line", "start": [-3, -4], "end": [3, -4], "width": 0.05},
        {"type": "circle", "center": [-5, 0], "radius": 1.2},  # around a pad row
        {"type": "line", "start": [-5.5, -3.175], "end": [-4.5, -3.175]},  # wholly on pad 1
        {"type": "line", "start": [-7, 0], "end": [7, 0], "layer": "F.Fab"},  # fab may cross copper
    ]}
    fp, _ = build(spec_of(raw), "U1")
    assert off_copper(fp)
    assert fp.fixes == ["line 1: trimmed where it ran over a pad",
                        f"line 2: 0.05 mm wide doesn't print; widened to {SILK_WIDTH} mm",
                        "circle 3: trimmed where it ran over a pad",
                        "line 4: left out, it lay wholly over pads"]
    assert any(ln.width == SILK_WIDTH and ln.y1 == -4 for ln in silk(fp))
    assert fp.circles == []  # the trimmed circle became short strokes
    assert [ln for ln in fp.lines if ln.layer == "F.Fab" and (ln.x1, ln.x2) == (-7, 7)]


@pytest.mark.parametrize("graphic, why", [
    ({"type": "text", "at": [-5, 0], "text": "X"}, "sits on a pad"),
    ({"type": "text", "at": [0, 0], "text": "tiny", "size": 0.5}, "fills in"),
    ({"type": "circle", "center": [-5, 0], "radius": 1.2, "fill": True}, "can't be trimmed"),
    ({"type": "polygon", "points": [[-6, -1], [-4, -1], [-4, 1], [-6, 1]], "fill": True}, "can't be trimmed"),
    ({"type": "arc", "start": [0, 0], "mid": [1, 0], "end": [2, 0]}, "straight line"),
    ({"type": "polygon", "points": [[0, 0], [1, 0]]}, "three points"),
    ({"type": "circle", "center": [0, 0], "radius": 0}, "radius"),
])
def test_what_cant_be_repaired_is_refused(graphic, why):
    with pytest.raises(Refused) as e:
        build(spec_of({**MODULE, "graphics": [graphic]}), "U1")
    assert why in e.value.errors[0]


def test_pad_labels_are_placed_clear_of_copper():
    raw = {**MODULE, "labels": {"text": {"1": "GND", "2": "3V3", "7": "IO0", "12": "EN"}}}
    fp, _ = build(spec_of(raw), "U1")
    assert [t.text for t in fp.texts] == ["GND", "3V3", "IO0", "EN"]
    by = {p.number: p for p in fp.pads}
    boxes = graphics.keepouts(fp.pads, 0)
    for t in fp.texts:
        box = graphics.text_box(t.x, t.y, t.text, t.size, t.rotation)
        assert not graphics._hits(box, boxes) and t.layer == "F.SilkS" and t.size == 1.0
    gnd, io0 = fp.texts[0], fp.texts[2]
    assert gnd.y == by["1"].y and gnd.x < by["1"].bbox[0]  # outside: left of the left row
    assert io0.y == by["7"].y and io0.x > by["7"].bbox[2]
    inside = build(spec_of({**raw, "labels": {**raw["labels"], "side": "inside"}}), "U1")[0]
    assert inside.texts[0].x > by["1"].bbox[2]
    with pytest.raises(Refused) as e:
        build(spec_of({**raw, "labels": {"text": {"99": "X"}}}), "U1")
    assert "no pad 99" in e.value.errors[0]
    tight = {**MODULE, "rows": [{**MODULE["rows"][0], "pitch": 0.9, "height": 0.5}], "labels": {"text": {"1": "A", "2": "B"}}}
    with pytest.raises(Refused) as e:
        build(spec_of(tight), "U1")
    assert "no room" in e.value.errors[0]


def test_labels_along_the_top_and_bottom_are_turned():
    raw = {"family": "custom", "package": "J", "source": "p.1", "body_x": 8, "body_y": 3,
           "rows": [{"x": -2.54, "y": 2, "count": 3, "pitch": 2.54, "direction": "right", "width": 1.2, "height": 2}],
           "labels": {"text": {"1": "V+", "2": "D", "3": "GND"}}}
    fp, _ = build(spec_of(raw), "J1")
    assert all(t.rotation == 90 and t.y > 3 and t.x == p.x for t, p in zip(fp.texts, fp.pads))


def test_a_polygon_body_shapes_fab_silkscreen_and_courtyard():
    ell = [[-4, -3], [4, -3], [4, 1], [0, 1], [0, 3], [-4, 3]]  # an L-shaped connector body
    raw = {"family": "custom", "package": "L", "source": "p.1", "body_x": 0, "body_y": 0, "body_outline": ell,
           "pads": [{"number": "1", "x": -2, "y": 4, "width": 1, "height": 1.5},
                    {"number": "2", "x": 2, "y": 2.2, "width": 1, "height": 1.5}]}
    fp, _ = build(spec_of(raw), "J1")
    fab = [ln for ln in fp.lines if ln.layer == "F.Fab"]
    assert len(fab) == 6 and {(ln.x1, ln.y1) for ln in fab} == {tuple(map(float, p)) for p in ell}
    grown = graphics.grow([tuple(p) for p in ell], 0.11)
    assert grown[0] == (-4.11, -3.11) and grown[3] == (0.11, 1.11)  # outward at convex and concave corners alike
    assert off_copper(fp) and len(silk(fp)) >= 6
    crt = [ln for ln in fp.lines if ln.layer == "F.CrtYd"]
    assert min(ln.y1 for ln in crt) == -3.25 and max(ln.y1 for ln in crt) == 5.0  # body box and pads
    with pytest.raises(Refused):
        build(spec_of({**raw, "body_outline": [[0, 0], [1, 1]]}), "J1")


def test_geometry_helpers():
    parts, cut = graphics.clip((0, 0), (10, 0), [(2, -1, 4, 1), (6, -1, 7, 1)])
    assert cut and parts == [((0, 0), (2, 0)), ((4, 0), (6, 0)), ((7, 0), (10, 0))]
    assert graphics.clip((0, 0), (10, 10), [(20, 20, 30, 30)]) == ([((0, 0), (10, 10))], False)
    diag, _ = graphics.clip((0, 0), (10, 10), [(4, 4, 6, 6)])  # a slanted line is cut too
    assert diag == [((0, 0), (4, 4)), ((6, 6), (10, 10))]
    arc = graphics.arc_points((-1, 0), (0, -1), (1, 0))
    assert all(abs((x * x + y * y) ** 0.5 - 1) < 1e-9 for x, y in arc) and all(y <= 1e-9 for _, y in arc)
    assert graphics.arc_points((0, 0), (1, 0), (2, 0)) is None
    square = [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert graphics.contains(square, (1, 1)) and not graphics.contains(square, (3, 1))
    assert graphics.covers(square, (1.5, 1.5, 3, 3)) and not graphics.covers(square, (2.5, 2.5, 3, 3))


def test_the_tool_refuses_with_reasons_and_labels_from_the_symbol(tmp_path, design, monkeypatch):
    from pcb_assistant.agent.agent import build_toolset

    monkeypatch.setattr(stock, "matches", lambda spec, lib=None: [])
    (tmp_path / "mine.kicad_sym").write_text(SYMBOL_LIB)
    create = build_toolset("test", tmp_path, design).tools["create_footprint"].function
    bad = {**MODULE, "graphics": [{"type": "text", "at": [-5, 0], "text": "X"}]}
    out = create(part="U1", spec=bad, propose=True)
    assert out.startswith("Not drawn as asked") and "sits on a pad" in out
    assert not (tmp_path / ".pcb_assistant" / "footprint_proposals.json").exists()
    spec = {"family": "custom", "package": "MOD", "source": "engineer", "body_x": 6, "body_y": 8,
            "rows": [{"x": -3, "y": -2.25, "pitch": 1.5, "direction": "down", "width": 1.2, "height": 0.9,
                      "numbers": ["1", "2", "3", "10"]}],
            "labels": {"from_symbol": True, "text": {"1": "0V"}}}
    assert "needs \"symbol\"" in create(part="U1", spec=spec, propose=True)
    create(part="U1", spec={**spec, "symbol": "mine:RADIO-MOD"}, propose=True)
    (p,) = proposals.FootprintStore(tmp_path).list()
    # pin names fill in around the label given by hand; the unnamed pin 3 gets none
    assert p.spec["labels"]["text"] == {"1": "0V"} and p.spec["labels"]["auto"] == {"2": "3V3", "10": "IO0"}
    assert p.kicad_mod.count("(fp_text user") == 4  # three labels and the fab reference
    assert check(spec_of(p.spec))[0] == []
