"""The approval card's drawing, straight from the built footprint: copper with
pad numbers, silkscreen, fab outline and courtyard, in mm (+y down, as KiCad).
kicad-cli's own plot is for printing; at card size its fab text and sketched
pad outlines turn into scribble."""
from __future__ import annotations

from html import escape

from pcb_assistant.footprints.graphics import arc_points, text_box
from pcb_assistant.footprints.kicad_mod import NPTH, Footprint, _num

COPPER = "#D68A4C"
PASTE = "#EEA766"
SILK = "#E8E1B0"
FAB = "#6B7C74"
COURTYARD = "#A374CE"
HOLE = "#0A0F0D"
NUMBER = "#0A0F0D"
NUMBER_ON_HOLE = "#E4EAE6"
COMMENT = "#7FA6C9"
MARGIN = 0.4  # mm around the courtyard
LAYER = {"F.SilkS": SILK, "F.Fab": FAB, "Cmts.User": COMMENT}  # layers the model can draw on


def _shape(tag: str, g, fill: bool) -> str:
    """A drawn outline (or filled shape) in its layer's colour."""
    color = LAYER[g.layer]
    paint = f'fill="{color}"' if fill else f'fill="none" stroke="{color}" stroke-width="{_num(g.width)}"'
    return f'{tag} {paint} stroke-linecap="round" stroke-linejoin="round"/>'


def _line(ln, color: str, dash: str = "") -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{_num(ln.x1)}" y1="{_num(ln.y1)}" x2="{_num(ln.x2)}" y2="{_num(ln.y2)}" '
            f'stroke="{color}" stroke-width="{_num(ln.width)}" stroke-linecap="round"{d}/>')


def _pad(p, fill: str, opacity: float = 1.0) -> str:
    x, y = p.x - p.w / 2, p.y - p.h / 2
    if p.shape in ("circle", "oval"):
        r = min(p.w, p.h) / 2
    elif p.shape == "roundrect":
        r = min(0.25, 0.25 * min(p.w, p.h))
    else:
        r = 0
    if p.kind == NPTH:  # a bare hole: a ring where copper must stay away, no land
        out = (f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(p.w)}" height="{_num(p.h)}" rx="{_num(r)}" '
               f'fill="{HOLE}" stroke="{FAB}" stroke-width="0.06"/>')
    else:
        out = (f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(p.w)}" height="{_num(p.h)}" rx="{_num(r)}" '
               f'fill="{fill}" fill-opacity="{opacity}"/>')
        if p.drill and p.drill_h:  # a slot
            out += (f'<rect x="{_num(p.x - p.drill / 2)}" y="{_num(p.y - p.drill_h / 2)}" width="{_num(p.drill)}" '
                    f'height="{_num(p.drill_h)}" rx="{_num(min(p.drill, p.drill_h) / 2)}" fill="{HOLE}"/>')
        elif p.drill:
            out += f'<circle cx="{_num(p.x)}" cy="{_num(p.y)}" r="{_num(p.drill / 2)}" fill="{HOLE}"/>'
    if p.rotation:  # KiCad turns counter-clockwise on a y-down sheet; SVG turns clockwise
        out = f'<g transform="rotate({_num(-p.rotation)} {_num(p.x)} {_num(p.y)})">{out}</g>'
    return out


def _number(p) -> str:
    # sized to the pad's short side (to the hole on a drilled pad, light on its dark)
    size = max(0.12, min((p.drill or min(p.w, p.h)) * 0.5, 1.2))
    return (f'<text x="{_num(p.x)}" y="{_num(p.y + size * 0.36)}" font-size="{_num(size)}" '
            f'text-anchor="middle" font-family="IBM Plex Mono, Consolas, monospace" font-weight="600" '
            f'fill="{NUMBER_ON_HOLE if p.drill else NUMBER}">{escape(p.number)}</text>')


def drawing(fp: Footprint) -> str:
    crt = [ln for ln in fp.lines if ln.layer == "F.CrtYd"]
    pts = [(ln.x1, ln.y1) for ln in crt] + [(ln.x2, ln.y2) for ln in crt]
    pts += [(v, w) for p in fp.pads for v, w in ((p.bbox[0], p.bbox[1]), (p.bbox[2], p.bbox[3]))]
    # the model's own graphics can reach past the courtyard: labels beside pads, a note
    pts += [pt for ln in fp.lines if ln.layer in LAYER for pt in ((ln.x1, ln.y1), (ln.x2, ln.y2))]
    pts += [pt for pg in fp.polys for pt in pg.points]
    pts += [pt for c in fp.circles for pt in ((c.x - c.r, c.y - c.r), (c.x + c.r, c.y + c.r))]
    pts += [pt for a in fp.arcs for pt in (arc_points((a.x1, a.y1), (a.xm, a.ym), (a.x2, a.y2)) or [])]
    for t in fp.texts:
        box = text_box(t.x, t.y, t.text, t.size, t.rotation)
        pts += [(box[0], box[1]), (box[2], box[3])]
    x0, y0 = min(x for x, _ in pts) - MARGIN, min(y for _, y in pts) - MARGIN
    w, h = max(x for x, _ in pts) + MARGIN - x0, max(y for _, y in pts) + MARGIN - y0
    body = [_line(ln, FAB) for ln in fp.lines if ln.layer == "F.Fab"]
    body += [_line(ln, COURTYARD, "0.15 0.12") for ln in crt]
    body += [_pad(p, COPPER) for p in fp.pads if p.solid]
    body += [_pad(p, PASTE, 0.55) for p in fp.pads if not p.solid]  # paste windows on a thermal pad
    body += [_line(ln, SILK) for ln in fp.lines if ln.layer == "F.SilkS"]
    body += [f'<polygon points="{" ".join(f"{_num(x)},{_num(y)}" for x, y in pg.points)}" fill="{SILK}"/>'
             for pg in fp.polys if pg.layer == "F.SilkS" and pg.fill]
    # what the model drew itself, on whichever layer
    body += [_line(ln, LAYER[ln.layer]) for ln in fp.lines if ln.layer in LAYER and ln.layer != "F.SilkS"]
    body += [_shape(f'<polygon points="{" ".join(f"{_num(x)},{_num(y)}" for x, y in pg.points)}"', pg, False)
             for pg in fp.polys if pg.layer in LAYER and not (pg.layer == "F.SilkS" and pg.fill)]
    body += [_shape(f'<circle cx="{_num(c.x)}" cy="{_num(c.y)}" r="{_num(c.r)}"', c, c.fill)
             for c in fp.circles if c.layer in LAYER]
    for a in fp.arcs:
        pts = arc_points((a.x1, a.y1), (a.xm, a.ym), (a.x2, a.y2)) or [(a.x1, a.y1), (a.x2, a.y2)]
        if a.layer in LAYER:
            body.append(_shape(f'<polyline points="{" ".join(f"{_num(x)},{_num(y)}" for x, y in pts)}"', a, False))
    for t in fp.texts:
        if t.layer in LAYER:
            turn = f' transform="rotate({_num(-t.rotation)} {_num(t.x)} {_num(t.y)})"' if t.rotation else ""
            body.append(f'<text x="{_num(t.x)}" y="{_num(t.y + t.size * 0.36)}" font-size="{_num(t.size)}" '
                        f'text-anchor="middle" font-family="IBM Plex Mono, Consolas, monospace" '
                        f'fill="{LAYER[t.layer]}"{turn}>{escape(t.text)}</text>')
    body += [_number(p) for p in fp.pads if p.number]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(w)}mm" height="{_num(h)}mm" '
            f'viewBox="{_num(x0)} {_num(y0)} {_num(w)} {_num(h)}">' + "".join(body) + "</svg>")
