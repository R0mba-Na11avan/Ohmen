"""The approval card's drawing, straight from the built footprint: copper with
pad numbers, silkscreen, fab outline and courtyard, in mm (+y down, as KiCad).
kicad-cli's own plot is for printing; at card size its fab text and sketched
pad outlines turn into scribble."""
from __future__ import annotations

from html import escape

from pcb_assistant.footprints.kicad_mod import Footprint, _num

COPPER = "#D68A4C"
PASTE = "#EEA766"
SILK = "#E8E1B0"
FAB = "#6B7C74"
COURTYARD = "#A374CE"
HOLE = "#0A0F0D"
NUMBER = "#0A0F0D"
NUMBER_ON_HOLE = "#E4EAE6"
MARGIN = 0.4  # mm around the courtyard


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
    out = (f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(p.w)}" height="{_num(p.h)}" rx="{_num(r)}" '
           f'fill="{fill}" fill-opacity="{opacity}"/>')
    if p.drill:
        out += f'<circle cx="{_num(p.x)}" cy="{_num(p.y)}" r="{_num(p.drill / 2)}" fill="{HOLE}"/>'
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
    x0, y0 = min(x for x, _ in pts) - MARGIN, min(y for _, y in pts) - MARGIN
    w, h = max(x for x, _ in pts) + MARGIN - x0, max(y for _, y in pts) + MARGIN - y0
    body = [_line(ln, FAB) for ln in fp.lines if ln.layer == "F.Fab"]
    body += [_line(ln, COURTYARD, "0.15 0.12") for ln in crt]
    body += [_pad(p, COPPER) for p in fp.pads if p.number]
    body += [_pad(p, PASTE, 0.55) for p in fp.pads if not p.number]  # paste windows on a thermal pad
    body += [_line(ln, SILK) for ln in fp.lines if ln.layer == "F.SilkS"]
    body += [f'<polygon points="{" ".join(f"{_num(x)},{_num(y)}" for x, y in pg.points)}" fill="{SILK}"/>'
             for pg in fp.polys if pg.layer == "F.SilkS"]
    body += [_number(p) for p in fp.pads if p.number]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(w)}mm" height="{_num(h)}mm" '
            f'viewBox="{_num(x0)} {_num(y0)} {_num(w)} {_num(h)}">' + "".join(body) + "</svg>")
