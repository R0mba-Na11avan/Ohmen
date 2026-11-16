"""A footprint's pads and graphics, written as a KiCad .kicad_mod S-expression."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

FORMAT_VERSION = 20241229  # KiCad 9's, the oldest Ohmen supports; newer ones upgrade on save
GENERATOR = "ohmen"

SMD_LAYERS = ("F.Cu", "F.Mask", "F.Paste")
SMD_NO_PASTE = ("F.Cu", "F.Mask")
PASTE_ONLY = ("F.Paste",)
THT_LAYERS = ("*.Cu", "*.Mask")
NPTH = "np_thru_hole"

SILK_WIDTH = 0.12
FAB_WIDTH = 0.10
CRTYD_WIDTH = 0.05


@dataclass
class Pad:
    number: str
    x: float
    y: float
    w: float
    h: float
    shape: str = "roundrect"  # roundrect | rect | circle | oval | chamfered_rect
    kind: str = "smd"  # smd | thru_hole | np_thru_hole (a bare hole: no number)
    drill: float | None = None
    layers: tuple[str, ...] = SMD_LAYERS
    rotation: float = 0.0  # degrees, as KiCad's (at x y angle); w and h are the unrotated pad's
    drill_h: float | None = None  # a slot: drill is its width, this its height (unrotated)
    chamfer: tuple[str, ...] = ()  # chamfered_rect corners: top_left, top_right, bottom_left, bottom_right
    chamfer_ratio: float = 0.2

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """The box around the pad as it sits on the board (rotation included)."""
        w, h = self.w, self.h
        if self.rotation % 180:
            a = math.radians(self.rotation)
            c, s = abs(math.cos(a)), abs(math.sin(a))
            w, h = round(self.w * c + self.h * s, 6), round(self.w * s + self.h * c, 6)
        return (self.x - w / 2, self.y - h / 2, self.x + w / 2, self.y + h / 2)

    @property
    def solid(self) -> bool:
        """Copper or a hole: what silkscreen and other parts keep off (not a paste window)."""
        return bool(self.number) or self.kind == NPTH


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str
    width: float


@dataclass
class Poly:
    points: list[tuple[float, float]]
    layer: str
    width: float
    fill: bool = True


@dataclass
class Footprint:
    name: str
    descr: str
    tags: str
    smd: bool = True
    pads: list[Pad] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    polys: list[Poly] = field(default_factory=list)
    ref_y: float = 0.0
    value_y: float = 0.0
    fab_text_size: float = 1.0


def _num(v: float) -> str:
    s = f"{round(v, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _text_effects(size: float, depth: int) -> list[str]:
    t = "\t" * depth
    thick = _num(max(0.05, round(size * 0.15, 3)))
    return [f"{t}(effects", f"{t}\t(font", f"{t}\t\t(size {_num(size)} {_num(size)})",
            f"{t}\t\t(thickness {thick})", f"{t}\t)", f"{t})"]


def _stroke(width: float) -> list[str]:
    return ["\t\t(stroke", f"\t\t\t(width {_num(width)})", "\t\t\t(type solid)", "\t\t)"]


def to_sexpr(fp: Footprint) -> str:
    out = [f"(footprint {_str(fp.name)}", f"\t(version {FORMAT_VERSION})", f"\t(generator {_str(GENERATOR)})",
           '\t(layer "F.Cu")', f"\t(descr {_str(fp.descr)})", f"\t(tags {_str(fp.tags)})"]
    for prop, value, y, layer in (("Reference", "REF**", fp.ref_y, "F.SilkS"),
                                  ("Value", fp.name, fp.value_y, "F.Fab")):
        out += [f"\t(property {_str(prop)} {_str(value)}", f"\t\t(at 0 {_num(y)} 0)",
                f"\t\t(layer {_str(layer)})", *_text_effects(1.0, 2), "\t)"]
    out.append(f"\t(attr {'smd' if fp.smd else 'through_hole'})")
    for ln in fp.lines:
        out += ["\t(fp_line", f"\t\t(start {_num(ln.x1)} {_num(ln.y1)})", f"\t\t(end {_num(ln.x2)} {_num(ln.y2)})",
                *_stroke(ln.width), f"\t\t(layer {_str(ln.layer)})", "\t)"]
    for pg in fp.polys:
        pts = " ".join(f"(xy {_num(x)} {_num(y)})" for x, y in pg.points)
        out += ["\t(fp_poly", f"\t\t(pts {pts})", *_stroke(pg.width),
                f"\t\t(fill {'yes' if pg.fill else 'no'})", f"\t\t(layer {_str(pg.layer)})", "\t)"]
    out += ['\t(fp_text user "${REFERENCE}"', "\t\t(at 0 0 0)", '\t\t(layer "F.Fab")',
            *_text_effects(fp.fab_text_size, 2), "\t)"]
    for p in fp.pads:
        chamfered = p.shape == "chamfered_rect"  # KiCad's: a roundrect with no rounding and cut corners
        at = f"{_num(p.x)} {_num(p.y)}" + (f" {_num(p.rotation)}" if p.rotation else "")
        out += [f"\t(pad {_str(p.number)} {p.kind} {'roundrect' if chamfered else p.shape}", f"\t\t(at {at})",
                f"\t\t(size {_num(p.w)} {_num(p.h)})"]
        if p.drill is not None and p.drill_h is not None:
            out.append(f"\t\t(drill oval {_num(p.drill)} {_num(p.drill_h)})")
        elif p.drill is not None:
            out.append(f"\t\t(drill {_num(p.drill)})")
        out.append("\t\t(layers " + " ".join(_str(layer) for layer in p.layers) + ")")
        if chamfered:
            out += ["\t\t(roundrect_rratio 0)", f"\t\t(chamfer_ratio {_num(p.chamfer_ratio)})",
                    "\t\t(chamfer " + " ".join(p.chamfer) + ")"]
        elif p.shape == "roundrect":
            out.append(f"\t\t(roundrect_rratio {_num(min(0.25, 0.25 / min(p.w, p.h)))})")
        out.append("\t)")
    out += ["\t(embedded_fonts no)", ")"]
    return "\n".join(out) + "\n"
