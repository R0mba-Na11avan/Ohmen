"""A checked spec becomes a footprint: IPC lands, then fab outline, silkscreen,
courtyard and text, laid out the way KiCad's own library does it.

Coordinates are mm with +y down. SMD packages sit on their centre; pin headers
on pin 1, as KiCad's do.
"""
from __future__ import annotations

import math
import re

from pcb_assistant.footprints import graphics
from pcb_assistant.footprints import ipc7351 as ipc
from pcb_assistant.footprints.graphics import SILK_MIN_SEGMENT, SILK_PAD_CLEARANCE
from pcb_assistant.footprints.kicad_mod import (
    CRTYD_WIDTH,
    FAB_WIDTH,
    NPTH,
    PASTE_ONLY,
    SILK_WIDTH,
    SMD_NO_PASTE,
    THT_LAYERS,
    Circle,
    Footprint,
    Line,
    Pad,
    Poly,
)
from pcb_assistant.footprints.spec import (
    Chip,
    Custom,
    Dim,
    DualGullwing,
    DualNoLead,
    PinHeader,
    QuadGullwing,
    QuadNoLead,
    Refused,
    drill_size,
)

SILK_OFFSET = 0.11  # silkscreen outside the body outline
EP_CLEARANCE = 0.2  # lands kept clear of an exposed pad
PASTE_COVERAGE = 0.65  # of an exposed pad's area, split into windows


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def default_name(spec, part: str) -> str:
    pkg = _safe(re.split(r"[\s(]", spec.package.strip(), maxsplit=1)[0]) or spec.family
    name = _safe(part)
    return name if name.startswith(pkg) else f"{pkg}_{name}"  # DFE252012F-4R7M, not DFE252012F_DFE252012F-4R7M


def _ys(n: int, pitch: float) -> list[float]:
    """Centres of n leads at a pitch, symmetric about 0."""
    return [round((i - (n - 1) / 2) * pitch, 4) for i in range(n)]


def _exposed(number: str, size: tuple[Dim, Dim]) -> list[Pad]:
    """The thermal pad (copper + mask) and its paste, split into windows so
    the part doesn't float on one big blob of solder."""
    w, h = round(size[0].mid, 4), round(size[1].mid, 4)
    pads = [Pad(number, 0, 0, w, h, layers=SMD_NO_PASTE)]
    nx, ny = max(1, round(w / 1.25)), max(1, round(h / 1.25))
    k = math.sqrt(PASTE_COVERAGE)
    cw, ch = w / nx, h / ny
    for i in range(nx):
        for j in range(ny):
            pads.append(Pad("", round(-w / 2 + cw * (i + 0.5), 4), round(-h / 2 + ch * (j + 0.5), 4),
                            round(cw * k, 3), round(ch * k, 3), layers=PASTE_ONLY))
    return pads


def _keep_clear_of_ep(pads: list[Pad], ep: tuple[Dim, Dim] | None, warnings: list[str]) -> None:
    """Shorten lands whose inner end comes within EP_CLEARANCE of the exposed pad."""
    if not ep:
        return
    hx, hy = ep[0].mid / 2 + EP_CLEARANCE, ep[1].mid / 2 + EP_CLEARANCE
    trimmed = False
    for p in pads:
        x0, y0, x1, y1 = p.bbox
        if x1 <= -hx + 1e-6 or x0 >= hx - 1e-6 or y1 <= -hy + 1e-6 or y0 >= hy - 1e-6:
            continue
        if p.w >= p.h:  # a land pointing in from the left or right
            edge, outer = (hx, x1) if p.x > 0 else (-hx, x0)
            p.w = round(abs(outer - edge), 4)
            p.x = round((outer + edge) / 2, 4)
        else:
            edge, outer = (hy, y1) if p.y > 0 else (-hy, y0)
            p.h = round(abs(outer - edge), 4)
            p.y = round((outer + edge) / 2, 4)
        trimmed = True
    if trimmed:
        warnings.append(f"lands shortened to keep {EP_CLEARANCE} mm from the exposed pad")


def _dual(numbers_by_pos: dict[int, str], per: int, pitch: float, land: ipc.Land) -> list[Pad]:
    pads = []
    for i, y in enumerate(_ys(per, pitch)):
        if (n := numbers_by_pos.get(i + 1)) is not None:  # left column, top to bottom
            pads.append(Pad(n, -land.center, y, land.length, land.x))
        if (n := numbers_by_pos.get(2 * per - i)) is not None:  # right column, bottom to top
            pads.append(Pad(n, land.center, y, land.length, land.x))
    return sorted(pads, key=lambda p: int(p.number))


def _quad(per: int, pitch: float, lx: ipc.Land, ly: ipc.Land) -> list[Pad]:
    pads: list[Pad] = []
    ys, n = _ys(per, pitch), 1
    for y in ys:  # left, top to bottom
        pads.append(Pad(str(n), -lx.center, y, lx.length, lx.x))
        n += 1
    for x in ys:  # bottom, left to right
        pads.append(Pad(str(n), x, ly.center, ly.x, ly.length))
        n += 1
    for y in reversed(ys):  # right, bottom to top
        pads.append(Pad(str(n), lx.center, y, lx.length, lx.x))
        n += 1
    for x in reversed(ys):  # top, right to left
        pads.append(Pad(str(n), x, -ly.center, ly.x, ly.length))
        n += 1
    return pads


def _lands(spec, warnings: list[str]) -> tuple[list[Pad], float, float, float, float]:
    """(pads, body centre x, y, body width, height)."""
    if isinstance(spec, DualGullwing):
        land = ipc.land(spec.lead_span, spec.lead_length, spec.lead_width,
                        ipc.gullwing_fillets(spec.pitch), spec.pitch)
        kept = [p for p in range(1, spec.positions + 1) if p not in set(spec.skip_positions)]
        pads = _dual({pos: str(i) for i, pos in enumerate(kept, 1)}, spec.positions // 2, spec.pitch, land)
        if spec.exposed_pad:
            pads += _exposed(str(len(kept) + 1), spec.exposed_pad)
        return pads, 0, 0, spec.body_width.mid, spec.body_length.mid
    if isinstance(spec, QuadGullwing):
        j = ipc.gullwing_fillets(spec.pitch)
        lx = ipc.land(spec.lead_span_x, spec.lead_length, spec.lead_width, j, spec.pitch)
        ly = ipc.land(spec.lead_span_y, spec.lead_length, spec.lead_width, j, spec.pitch)
        pads = _quad(spec.pins // 4, spec.pitch, lx, ly)
        if spec.exposed_pad:
            _keep_clear_of_ep(pads, spec.exposed_pad, warnings)
            pads += _exposed(str(spec.pins + 1), spec.exposed_pad)
        return pads, 0, 0, spec.body_x.mid, spec.body_y.mid
    if isinstance(spec, DualNoLead):
        land = ipc.land(spec.body_width, spec.terminal_length, spec.terminal_width, ipc.NO_LEAD, spec.pitch)
        per = spec.pins // 2
        pads = _dual({p: str(p) for p in range(1, spec.pins + 1)}, per, spec.pitch, land)
        if spec.exposed_pad:
            _keep_clear_of_ep(pads, spec.exposed_pad, warnings)
            pads += _exposed(str(spec.pins + 1), spec.exposed_pad)
        return pads, 0, 0, spec.body_width.mid, spec.body_length.mid
    if isinstance(spec, QuadNoLead):
        lx = ipc.land(spec.body_x, spec.terminal_length, spec.terminal_width, ipc.NO_LEAD, spec.pitch)
        ly = ipc.land(spec.body_y, spec.terminal_length, spec.terminal_width, ipc.NO_LEAD, spec.pitch)
        pads = _quad(spec.pins // 4, spec.pitch, lx, ly)
        if spec.exposed_pad:
            _keep_clear_of_ep(pads, spec.exposed_pad, warnings)
            pads += _exposed(str(spec.pins + 1), spec.exposed_pad)
        return pads, 0, 0, spec.body_x.mid, spec.body_y.mid
    if isinstance(spec, Chip):
        land = ipc.land(spec.body_length, spec.terminal_length, spec.body_width, ipc.chip_fillets(spec.body_length))
        pads = [Pad("1", -land.center, 0, land.length, land.x), Pad("2", land.center, 0, land.length, land.x)]
        return pads, 0, 0, spec.body_length.mid, spec.body_width.mid
    if isinstance(spec, PinHeader):
        row_pitch = spec.row_pitch or spec.pitch
        drill = ipc.tht_drill(spec.lead_diameter)
        size = ipc.tht_pad(drill, min(spec.pitch, row_pitch))
        pads = []
        for i in range(spec.pins_per_row):
            for r in range(spec.rows):
                n = i * spec.rows + r + 1
                pads.append(Pad(str(n), round(r * row_pitch, 4), round(i * spec.pitch, 4), size, size,
                                shape="rect" if n == 1 else "circle", kind="thru_hole", drill=drill,
                                layers=THT_LAYERS))
        w, h = spec.rows * row_pitch, spec.pins_per_row * spec.pitch
        return pads, (spec.rows - 1) * row_pitch / 2, (spec.pins_per_row - 1) * spec.pitch / 2, w, h
    if isinstance(spec, Custom):
        pads = []
        for p in spec.all_pads():
            hole = drill_size(p.drill)
            if hole:
                layers = THT_LAYERS if p.mask else ("*.Cu",)
            else:
                layers = ("F.Cu", *(("F.Mask",) if p.mask else ()), *(("F.Paste",) if p.paste else ()))
            pads.append(Pad(p.number, p.x, p.y, p.width, p.height, shape=p.shape,
                            kind="thru_hole" if hole else "smd", drill=hole[0] if hole else None,
                            layers=layers, rotation=p.rotation % 360,
                            drill_h=hole[1] if hole and hole[1] != hole[0] else None,
                            chamfer=tuple(p.chamfer), chamfer_ratio=p.chamfer_ratio))
        for h in spec.holes:  # bare holes: no copper, no number
            w, ht = drill_size(h.drill)
            pads.append(Pad("", h.x, h.y, w, ht, shape="circle" if w == ht else "oval", kind=NPTH, drill=w,
                            layers=THT_LAYERS, drill_h=ht if ht != w else None))
        return pads, spec.body_offset[0], spec.body_offset[1], spec.body_x, spec.body_y
    raise ValueError(f"unknown family {spec.family}")


def _rect_lines(x0: float, y0: float, x1: float, y1: float, layer: str, width: float) -> list[Line]:
    return [Line(x0, y0, x1, y0, layer, width), Line(x1, y0, x1, y1, layer, width),
            Line(x1, y1, x0, y1, layer, width), Line(x0, y1, x0, y0, layer, width)]


def _clip(line: Line, keepouts: list[tuple[float, float, float, float]]) -> list[Line]:
    """The parts of an axis-aligned line outside every keep-out box."""
    horizontal = line.y1 == line.y2
    a, b = sorted((line.x1, line.x2) if horizontal else (line.y1, line.y2))
    fixed = line.y1 if horizontal else line.x1
    spans = [(a, b)]
    for x0, y0, x1, y1 in keepouts:
        lo, hi, c0, c1 = (x0, x1, y0, y1) if horizontal else (y0, y1, x0, x1)
        if not c0 < fixed < c1:
            continue
        spans = [s for s0, s1 in spans for s in ((s0, min(s1, lo)), (max(s0, hi), s1)) if s[1] - s[0] > SILK_MIN_SEGMENT]
    out = []
    for s0, s1 in spans:
        if horizontal:
            out.append(Line(round(s0, 4), fixed, round(s1, 4), fixed, line.layer, line.width))
        else:
            out.append(Line(fixed, round(s0, 4), fixed, round(s1, 4), line.layer, line.width))
    return out


def _out(v: float, step: float = 0.01) -> float:
    """Round away from zero to the courtyard grid."""
    return round(math.copysign(math.ceil(abs(v) / step - 1e-9) * step, v), 4)


def build(spec, part: str) -> tuple[Footprint, list[str]]:
    """The footprint for a checked spec, and anything worth a second look."""
    warnings: list[str] = []
    pads, cx, cy, bw, bh = _lands(spec, warnings)
    name = spec.name.strip() or default_name(spec, part)
    fp = Footprint(
        name=_safe(name),
        descr=f"{spec.package} for {part}, from {spec.drawing or 'the datasheet'}; IPC-7351B nominal "
              f"(generated by Ohmen)",
        tags=f"{spec.package} {part} ohmen".strip(),
        # a surface-mount connector with a few through-hole tabs is still placed as SMD
        smd=sum(p.kind == "smd" for p in pads if p.number) > sum(p.kind == "thru_hole" for p in pads)
        or not any(p.kind == "thru_hole" for p in pads),
        pads=pads,
    )
    copper = [p for p in pads if p.number]
    solid = [p for p in pads if p.solid]  # copper and bare holes: what the courtyard and silkscreen mind
    for i, a in enumerate(copper):
        for b in copper[i + 1:]:
            dx = max(0.0, b.bbox[0] - a.bbox[2], a.bbox[0] - b.bbox[2])
            dy = max(0.0, b.bbox[1] - a.bbox[3], a.bbox[1] - b.bbox[3])
            gap = math.hypot(dx, dy)
            if gap < 0.15 - 1e-6 and a.number != b.number:
                warnings.append(f"pads {a.number} and {b.number} are only {gap:.2f} mm apart")

    outline = [(float(x), float(y)) for x, y in spec.body_outline]
    if outline:  # a body that isn't a plain rectangle: its box stands in wherever a box is needed
        if len(outline) < 3 or abs(graphics.area(outline)) < 1e-6:
            raise Refused(["body_outline needs three points that enclose an area"])
        bx0, by0 = min(x for x, _ in outline), min(y for _, y in outline)
        bx1, by1 = max(x for x, _ in outline), max(y for _, y in outline)
        cx, cy, bw, bh = (bx0 + bx1) / 2, (by0 + by1) / 2, bx1 - bx0, by1 - by0
    else:
        bx0, by0, bx1, by1 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
    polarized = _polarized(spec)
    if outline:
        fp.lines += [Line(*a, *b, "F.Fab", FAB_WIDTH) for a, b in zip(outline, outline[1:] + outline[:1])]
    else:
        # fab: the body, pin-1 corner chamfered when pin 1 matters
        c = min(1.0, 0.25 * min(bw, bh)) if polarized else 0
        corners = [(bx0 + c, by0), (bx1, by0), (bx1, by1), (bx0, by1), (bx0, by0 + c), (bx0 + c, by0)]
        corners = [pt for i, pt in enumerate(corners) if i == 0 or pt != corners[i - 1]]
        fp.lines += [Line(*corners[i], *corners[i + 1], "F.Fab", FAB_WIDTH) for i in range(len(corners) - 1)]

    cut = graphics.cut_and_keep(spec, fp)

    # courtyard: pads, body and any board cutout plus the excess, on the 0.01 grid
    xs = [v for p in solid for v in (p.bbox[0], p.bbox[2])] + [bx0, bx1] + [x for x, _ in cut]
    ys = [v for p in solid for v in (p.bbox[1], p.bbox[3])] + [by0, by1] + [y for _, y in cut]
    m = ipc.COURTYARD
    k0x, k0y, k1x, k1y = (_out(min(xs) - m), _out(min(ys) - m), _out(max(xs) + m), _out(max(ys) + m))
    fp.lines += _rect_lines(k0x, k0y, k1x, k1y, "F.CrtYd", CRTYD_WIDTH)

    # silkscreen: the body outline pushed out, minus wherever it would touch copper
    keep = SILK_PAD_CLEARANCE + SILK_WIDTH / 2
    keepouts = [(p.bbox[0] - keep, p.bbox[1] - keep, p.bbox[2] + keep, p.bbox[3] + keep) for p in solid]
    s = SILK_OFFSET
    style = spec.silk
    if style.outline == "none":
        pass
    elif outline:  # every edge of the polygon, whichever way it runs
        grown = graphics.grow(outline, s)
        for a, b in zip(grown, grown[1:] + grown[:1]):
            fp.lines += [Line(*p, *q, "F.SilkS", SILK_WIDTH) for p, q in graphics.clip(a, b, keepouts)[0]]
    elif style.outline == "corners":
        n = min(1.0, 0.3 * min(bw, bh))
        for x, dx in ((bx0 - s, n), (bx1 + s, -n)):
            for y, dy in ((by0 - s, n), (by1 + s, -n)):
                for ln in (Line(x, y, x + dx, y, "F.SilkS", SILK_WIDTH), Line(x, y, x, y + dy, "F.SilkS", SILK_WIDTH)):
                    fp.lines += _clip(ln, keepouts)
    else:
        for ln in _rect_lines(bx0 - s, by0 - s, bx1 + s, by1 + s, "F.SilkS", SILK_WIDTH):
            fp.lines += _clip(ln, keepouts)
    mark = style.pin1
    if mark == "auto":
        mark = "triangle" if polarized and style.polarity == "none" else "none"
    p1 = next((p for p in copper if p.number == "1"), copper[0]) if copper else None
    if p1 and mark == "triangle":
        fp.polys.append(_pin1_marker(p1, keepouts))
    elif p1 and mark != "none":
        _pin1_other(fp, p1, keepouts, mark)
    if p1 and style.polarity != "none":
        _polarity(fp, p1, style.polarity, (bx0 - s, by0 - s, bx1 + s, by1 + s), keep, keepouts)

    graphics.draw(spec, fp, solid)
    graphics.label(spec, fp, solid, (bx0, by0, bx1, by1))

    fp.ref_y = round(k0y - 0.9, 4)
    fp.value_y = round(k1y + 0.9, 4)
    fp.fab_text_size = max(0.25, min(1.0, round(min(bw, bh) * 0.4, 2)))
    return fp, warnings


def _polarized(spec) -> bool:
    """Whether pin 1 gets marked: not on an R, C, L or fuse, nor on a two-pad
    land pattern unless the spec says so."""
    if isinstance(spec, Chip):
        return spec.kind not in ("resistor", "capacitor", "inductor", "fuse")
    if isinstance(spec, Custom):
        return spec.polarized if spec.polarized is not None else len([p for p in spec.all_pads() if p.number]) > 2
    return True


def _clear(pts, keepouts) -> bool:
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    return not any(min(xs) < k[2] and max(xs) > k[0] and min(ys) < k[3] and max(ys) > k[1] for k in keepouts)


def _pin1_marker(p1: Pad, keepouts) -> Poly:
    """A small filled triangle pointing at pin 1: above it, or else to its left."""
    gap, depth, half = 0.27, 0.33, 0.24
    tip = (p1.x, p1.bbox[1] - gap)
    above = [tip, (tip[0] - half, tip[1] - depth), (tip[0] + half, tip[1] - depth)]
    tip = (p1.bbox[0] - gap, p1.y)
    left = [tip, (tip[0] - depth, tip[1] - half), (tip[0] - depth, tip[1] + half)]
    pts = above if _clear(above, keepouts) or not _clear(left, keepouts) else left
    return Poly([(round(x, 4), round(y, 4)) for x, y in pts], "F.SilkS", SILK_WIDTH)


def _pin1_other(fp: Footprint, p1: Pad, keepouts, mark: str) -> None:
    """Pin 1 marked with a dot, or a line along the pad: above it, or else to its left."""
    gap, r = 0.27, 0.15
    x0, y0, x1, y1 = p1.bbox
    if mark == "dot":
        above, left = (p1.x, y0 - gap - r), (x0 - gap - r, p1.y)

        def box(c):
            return [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]

        at = above if _clear(box(above), keepouts) or not _clear(box(left), keepouts) else left
        fp.circles.append(Circle(round(at[0], 4), round(at[1], 4), r, "F.SilkS", SILK_WIDTH, fill=True))
        return
    above, left = [(x0, y0 - gap), (x1, y0 - gap)], [(x0 - gap, y0), (x0 - gap, y1)]
    (ax, ay), (bx, by) = above if _clear(above, keepouts) or not _clear(left, keepouts) else left
    fp.lines.append(Line(round(ax, 4), round(ay, 4), round(bx, 4), round(by, 4), "F.SilkS", SILK_WIDTH))


def _polarity(fp: Footprint, p1: Pad, mark: str, box, keep: float, keepouts) -> None:
    """Pad 1's end of the part marked: a bar across it (a diode's cathode), or
    a + beside it (an electrolytic's positive lead)."""
    x0, y0, x1, y1 = box  # the silkscreen outline
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    px0, py0, px1, py1 = p1.bbox[0] - keep, p1.bbox[1] - keep, p1.bbox[2] + keep, p1.bbox[3] + keep
    arm, off, inset = 0.3, 0.55, 0.3
    if abs(p1.x - cx) >= abs(p1.y - cy):  # pad 1 at the left or right end
        first = p1.x < cx
        x = min(x0, px0) if first else max(x1, px1)
        top, bottom = min(y0, py0), max(y1, py1)
        if mark == "cathode_bar":
            if x in (x0, x1):  # the outline is already there: a second line just inside it
                x += inset if first else -inset
            fp.lines += _clip(Line(round(x, 4), round(top, 4), round(x, 4), round(bottom, 4),
                                   "F.SilkS", SILK_WIDTH), keepouts)
            return
        out = x - off if first else x + off
        spots = [(out, top - off), (out, bottom + off)]
    else:  # at the top or bottom end
        first = p1.y < cy
        y = min(y0, py0) if first else max(y1, py1)
        left, right = min(x0, px0), max(x1, px1)
        if mark == "cathode_bar":
            if y in (y0, y1):
                y += inset if first else -inset
            fp.lines += _clip(Line(round(left, 4), round(y, 4), round(right, 4), round(y, 4),
                                   "F.SilkS", SILK_WIDTH), keepouts)
            return
        out = y - off if first else y + off
        spots = [(left - off, out), (right + off, out)]

    def free(at):
        return _clear([(at[0] - arm, at[1] - arm), (at[0] + arm, at[1] + arm)], keepouts)

    ax, ay = (round(v, 4) for v in next((s for s in spots if free(s)), spots[0]))
    for ln in (Line(round(ax - arm, 4), ay, round(ax + arm, 4), ay, "F.SilkS", SILK_WIDTH),
               Line(ax, round(ay - arm, 4), ax, round(ay + arm, 4), "F.SilkS", SILK_WIDTH)):
        fp.lines += _clip(ln, keepouts)  # neither corner free: at least kept off copper
