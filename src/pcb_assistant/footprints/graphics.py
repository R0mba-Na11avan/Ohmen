"""What the model draws itself: lines, arcs, circles, polygons and text on the
silkscreen, fab and comment layers, and labels beside pads.

Silkscreen is held to the fab's rules. A repair that can't change what a mark
means is made and noted (a line trimmed off a pad, a hairline widened); one
that could is refused, with the reason, for the model to redraw.
"""
from __future__ import annotations

import math

from pcb_assistant.footprints.kicad_mod import FAB_WIDTH, SILK_WIDTH, Arc, Circle, Footprint, Line, Poly, Text, Zone
from pcb_assistant.footprints.spec import Refused

SILK = "F.SilkS"
SILK_PAD_CLEARANCE = 0.2  # silkscreen kept off copper and holes
SILK_MIN_SEGMENT = 0.2  # shorter scraps left between pads are dropped
MIN_SILK_WIDTH = 0.1  # thinner than this doesn't print
MIN_SILK_TEXT = 0.8  # text height below this fills in
MAX_LABEL = 1.0
CHAR_ASPECT = 0.9  # a character's advance, as a share of the text height (a little generous)
ARC_STEP = math.radians(6)
DEFAULT_WIDTH = {SILK: SILK_WIDTH, "F.Fab": FAB_WIDTH, "Cmts.User": 0.1}

Box = tuple[float, float, float, float]
Point = tuple[float, float]


def keepouts(solid, width: float = SILK_WIDTH) -> list[Box]:
    """Where a silkscreen stroke of this width may not run: each pad and hole, grown."""
    keep = SILK_PAD_CLEARANCE + width / 2
    return [(p.bbox[0] - keep, p.bbox[1] - keep, p.bbox[2] + keep, p.bbox[3] + keep) for p in solid]


def _inside(a: Point, b: Point, box: Box) -> tuple[float, float] | None:
    """The stretch of segment a-b inside the box, as (t0, t1) along it (Liang-Barsky)."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]), (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if abs(p) < 1e-12:
            if q <= 0:
                return None  # runs alongside, outside or on the edge
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return (t0, t1) if t1 - t0 > 1e-9 else None


def clip(a: Point, b: Point, boxes: list[Box]) -> tuple[list[tuple[Point, Point]], bool]:
    """The parts of a-b outside every box, and whether anything was cut away."""
    spans = [(0.0, 1.0)]
    cut = False
    for box in boxes:
        hit = _inside(a, b, box)
        if hit is None:
            continue
        cut = True
        spans = [s for s0, s1 in spans for s in ((s0, min(s1, hit[0])), (max(s0, hit[1]), s1)) if s[1] > s[0]]
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    out = []
    for s0, s1 in spans:
        if cut and (s1 - s0) * length <= SILK_MIN_SEGMENT:
            continue
        out.append(((round(a[0] + (b[0] - a[0]) * s0, 4), round(a[1] + (b[1] - a[1]) * s0, 4)),
                    (round(a[0] + (b[0] - a[0]) * s1, 4), round(a[1] + (b[1] - a[1]) * s1, 4))))
    return out, cut


def arc_points(start: Point, mid: Point, end: Point) -> list[Point] | None:
    """An arc through three points as a run of short chords; None if they're in line."""
    (ax, ay), (bx, by), (cx, cy) = start, mid, end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, a1, a2 = (math.atan2(y - uy, x - ux) for x, y in (start, mid, end))
    sweep, via = (a2 - a0) % math.tau, (a1 - a0) % math.tau
    if via > sweep:  # the middle point is on the other way round
        sweep -= math.tau
    n = max(2, math.ceil(abs(sweep) / ARC_STEP))
    return [(ux + r * math.cos(a0 + sweep * i / n), uy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


def circle_points(center: Point, radius: float) -> list[Point]:
    n = max(12, math.ceil(math.tau / ARC_STEP))
    return [(center[0] + radius * math.cos(math.tau * i / n), center[1] + radius * math.sin(math.tau * i / n))
            for i in range(n + 1)]


def area(points: list[Point]) -> float:
    """Signed area (shoelace)."""
    return sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2


def contains(points: list[Point], p: Point) -> bool:
    inside = False
    for i in range(len(points)):
        (x1, y1), (x2, y2) = points[i], points[(i + 1) % len(points)]
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def covers(points: list[Point], box: Box) -> bool:
    """Whether a closed polygon and a box share any area."""
    if contains(points, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)):
        return True
    return any(_inside(points[i], points[(i + 1) % len(points)], box) for i in range(len(points)))


def grow(points: list[Point], by: float) -> list[Point]:
    """A polygon pushed outward by a distance, corners kept sharp."""
    n = len(points)
    sign = 1 if area(points) > 0 else -1
    normals = []
    for i in range(n):
        (x1, y1), (x2, y2) = points[i], points[(i + 1) % n]
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        normals.append((sign * (y2 - y1) / length, -sign * (x2 - x1) / length))
    out = []
    for i in range(n):
        (ax, ay), (bx, by_) = normals[i - 1], normals[i]
        k = 1 + ax * bx + ay * by_
        mx, my = ((ax + bx) / k, (ay + by_) / k) if k > 1e-6 else (ax, ay)
        out.append((round(points[i][0] + by * mx, 4), round(points[i][1] + by * my, 4)))
    return out


def text_box(x: float, y: float, text: str, size: float, rotation: float = 0) -> Box:
    w, h = max(1, len(text)) * size * CHAR_ASPECT, size
    if rotation % 180:
        w, h = h, w
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _hits(box: Box, boxes: list[Box]) -> bool:
    return any(box[0] < k[2] and box[2] > k[0] and box[1] < k[3] and box[3] > k[1] for k in boxes)


def _where(p: Point) -> str:
    return f"({round(p[0], 3)}, {round(p[1], 3)})"


def _strokes(fp: Footprint, points: list[Point], width: float, boxes: list[Box]) -> bool:
    """Draw a run of points as silkscreen lines, trimmed off the boxes. True if trimmed."""
    trimmed = False
    for a, b in zip(points, points[1:]):
        parts, cut = clip(a, b, boxes)
        trimmed |= cut
        fp.lines += [Line(p[0], p[1], q[0], q[1], SILK, width) for p, q in parts]
    return trimmed


def draw(spec, fp: Footprint, solid) -> None:
    """Add the spec's own graphics to the footprint. Raises Refused."""
    errors: list[str] = []
    for i, g in enumerate(spec.graphics, 1):
        what = f"{g.type} {i}"
        silk = g.layer == SILK
        width = getattr(g, "width", None) or DEFAULT_WIDTH[g.layer]
        if width <= 0:
            errors.append(f"{what}: width must be positive")
            continue
        if silk and width < MIN_SILK_WIDTH:
            fp.fixes.append(f"{what}: {width} mm wide doesn't print; widened to {SILK_WIDTH} mm")
            width = SILK_WIDTH
        boxes = keepouts(solid, width) if silk else []

        if g.type == "text":
            if not g.text.strip():
                errors.append(f"{what}: no text")
            elif silk and g.size < MIN_SILK_TEXT:
                errors.append(f"{what} '{g.text}': {g.size} mm text fills in on silkscreen; "
                              f"use at least {MIN_SILK_TEXT} mm")
            elif silk and _hits(text_box(g.at[0], g.at[1], g.text, g.size, g.rotation), keepouts(solid, 0)):
                errors.append(f"{what} '{g.text}' at {_where(g.at)} sits on a pad; move it clear")
            else:
                fp.texts.append(Text(g.text, g.at[0], g.at[1], g.rotation, g.layer, g.size))
            continue

        if g.type == "line":
            points, closed = [g.start, g.end], False
        elif g.type == "arc":
            points, closed = arc_points(g.start, g.mid, g.end), False
            if points is None:
                errors.append(f"{what}: start, mid and end are in a straight line")
                continue
        elif g.type == "circle":
            if g.radius <= 0:
                errors.append(f"{what}: radius must be positive")
                continue
            points, closed = circle_points(g.center, g.radius), True
        else:
            if len(g.points) < 3 or abs(area(g.points)) < 1e-6:
                errors.append(f"{what}: needs three points that enclose an area")
                continue
            points, closed = [*g.points, g.points[0]], True

        filled = closed and g.fill
        if silk and filled and any(covers(points[:-1], k) for k in boxes):
            errors.append(f"{what}: a filled shape over a pad can't be trimmed; move or shrink it")
            continue
        if silk and any(_inside(a, b, k) for a, b in zip(points, points[1:]) for k in boxes):
            before = len(fp.lines)
            _strokes(fp, points, width, boxes)
            fp.fixes.append(f"{what}: trimmed where it ran over a pad" if len(fp.lines) > before
                            else f"{what}: left out, it lay wholly over pads")
        elif g.type == "line":
            fp.lines.append(Line(*g.start, *g.end, g.layer, width))
        elif g.type == "arc":
            fp.arcs.append(Arc(*g.start, *g.mid, *g.end, g.layer, width))
        elif g.type == "circle":
            fp.circles.append(Circle(*g.center, g.radius, g.layer, width, g.fill))
        else:
            fp.polys.append(Poly([(round(x, 4), round(y, 4)) for x, y in g.points], g.layer, width, g.fill))
    if errors:
        raise Refused(errors)


EDGE = "Edge.Cuts"
EDGE_WIDTH = 0.05
COPPER_LAYERS = {"front": ("F.Cu",), "back": ("B.Cu",), "all": ("*.Cu",)}


def _span(points: list[Point]) -> str:
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return f"{round(max(xs) - min(xs), 3)} x {round(max(ys) - min(ys), 3)} mm"


def cut_and_keep(spec, fp: Footprint) -> list[Point]:
    """Board cutouts and keepout areas, each held to its own rules; returns the
    cutouts' points for the courtyard to take in. Raises Refused.

    A wrong cutout is milled through the board, so nothing is repaired here:
    every doubt goes back to the model."""
    errors: list[str] = []
    copper = [p for p in fp.pads if p.number]
    reach: list[Point] = []
    for i, c in enumerate(spec.cutouts, 1):
        what = f"cutout {i}"
        pts = [(float(x), float(y)) for x, y in c.points]
        if not c.source.strip():
            errors.append(f"{what}: say where it is dimensioned (source); a cutout is never drawn from memory")
        if c.closed and (len(pts) < 3 or abs(area(pts)) < 1e-6):
            errors.append(f"{what}: a closed cutout needs three points that enclose an area")
            continue
        if not c.closed and len(pts) < 2:
            errors.append(f"{what}: a notch needs at least two points")
            continue
        edges = list(zip(pts, pts[1:] + pts[:1])) if c.closed else list(zip(pts, pts[1:]))
        hit = next((p for p in copper if (c.closed and covers(pts, p.bbox))
                    or any(_inside(a, b, p.bbox) for a, b in edges)), None)
        if hit:
            errors.append(f"{what} cuts through pad {hit.number}; check its position against the land pattern")
            continue
        fp.lines += [Line(round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4), EDGE, EDGE_WIDTH)
                     for a, b in edges]
        reach += pts
    for i, k in enumerate(spec.keepouts, 1):
        what = f"keepout {i}"
        pts = [(float(x), float(y)) for x, y in k.points]
        if not k.source.strip():
            errors.append(f"{what}: say where it is asked for (source)")
        if len(pts) < 3 or abs(area(pts)) < 1e-6:
            errors.append(f"{what}: needs three points that enclose an area")
            continue
        if not (k.tracks or k.vias or k.copper_pour):
            errors.append(f"{what}: keeps nothing out; ban tracks, vias or copper_pour")
            continue
        if k.tracks:  # a pad nothing may be routed to is a part that can't be connected
            hit = next((p for p in copper if covers(pts, p.bbox)
                        and (p.kind != "smd" or k.layers in ("front", "all"))), None)
            if hit:
                errors.append(f"{what} covers pad {hit.number}, which no track could then reach; "
                              f"draw it clear of the pads")
                continue
        fp.zones.append(Zone([(round(x, 4), round(y, 4)) for x, y in pts], COPPER_LAYERS[k.layers],
                             k.tracks, k.vias, k.copper_pour))
    if errors:
        raise Refused(errors)
    return reach


def risks(spec) -> list[str]:
    """What the engineer must see before accepting: this footprint changes the
    board itself, or constrains its layout."""
    out = []
    for c in spec.cutouts:
        kind = "a hole" if c.closed else "an open notch (its ends must meet your board outline)"
        out.append(f"Cuts the board: {kind}, {_span(c.points)}. From: {c.source}")
    for k in spec.keepouts:
        banned = ", ".join(n for n, on in (("tracks", k.tracks), ("vias", k.vias), ("copper pour", k.copper_pour)) if on)
        side = {"front": "front copper", "back": "back copper", "all": "all copper layers"}[k.layers]
        out.append(f"Keepout: no {banned} on {side}, {_span(k.points)}. From: {k.source}")
    return out


def drawn(spec) -> list[dict[str, str]]:
    """The marks the model asked for, for the card to list."""
    out = []
    for g in spec.graphics:
        if g.type == "line":
            detail = f"{_where(g.start)} to {_where(g.end)}"
        elif g.type == "arc":
            detail = f"{_where(g.start)} to {_where(g.end)} through {_where(g.mid)}"
        elif g.type == "circle":
            detail = f"radius {g.radius} at {_where(g.center)}" + (", filled" if g.fill else "")
        elif g.type == "polygon":
            detail = f"{len(g.points)} points" + (", filled" if g.fill else "")
        else:
            detail = f"'{g.text}' at {_where(g.at)}, {g.size} mm"
        out.append({"layer": g.layer, "type": g.type, "detail": detail})
    if spec.labels:
        out += [{"layer": SILK, "type": "label", "detail": f"pad {n}: '{t}'"}
                for n, t in {**spec.labels.auto, **spec.labels.text}.items()]
    return out


def label(spec, fp: Footprint, solid, body: Box) -> None:
    """Text beside pads, clear of copper: outside the part by default.

    A label asked for by hand (labels.text) that doesn't fit is refused. One
    filled in from the symbol (labels.auto) is left out and noted: asking for
    every pin's name shouldn't fail on the one pad in the middle. Raises Refused."""
    lab = spec.labels
    if lab is None or not (lab.text or lab.auto):
        return
    wanted = {**lab.auto, **lab.text}
    numbered = {}
    for p in fp.pads:
        if p.number:
            numbered.setdefault(p.number, p)  # shared numbers (shield tabs): the first one carries the label
    errors = [f"labels: no pad {n}" for n in lab.text if n not in numbered]

    def skip(number: str, why: str, fix: str) -> None:
        if number in lab.text:
            errors.append(f"labels: {why}; {fix}")
        else:
            fp.fixes.append(f"label '{wanted[number]}' left out: {why}")

    edge = SILK_PAD_CLEARANCE + SILK_WIDTH + 0.1
    boxes = keepouts(solid, 0)
    sides: dict[str, list] = {}
    for n in wanted:
        if n not in numbered:
            continue
        p = numbered[n]
        # the body edge the pad sits at (or past): a row along the bottom edge is labelled below
        gap, out = min((p.x - body[0], "left"), (body[2] - p.x, "right"), (p.y - body[1], "up"),
                       (body[3] - p.y, "down"))
        if gap > max(p.w, p.h):
            skip(n, f"pad {n} isn't at the body's edge", "place its label yourself as a text graphic")
            continue
        if lab.side == "inside":
            out = {"right": "left", "left": "right", "down": "up", "up": "down"}[out]
        sides.setdefault(out, []).append(p)
    for side, pads in sides.items():
        across = (lambda p: p.y) if side in ("left", "right") else (lambda p: p.x)
        spots = sorted(across(p) for p in pads)
        pitch = min((b - a for a, b in zip(spots, spots[1:])), default=None)
        size = lab.size or min(MAX_LABEL, round(pitch - 0.2, 2) if pitch is not None else MAX_LABEL)
        if size < MIN_SILK_TEXT or (pitch is not None and size > pitch - 0.1):
            why = (f"pads {pitch and round(pitch, 3)} mm apart leave no room for "
                   f"{MIN_SILK_TEXT} mm text")
            if any(p.number in lab.text for p in pads):
                errors.append(f"labels: {why}; label fewer pads or none")
            else:
                fp.fixes.append(f"{len(pads)} labels on the {side} side left out: {why}")
            continue
        for p in pads:
            text = wanted[p.number]
            half = max(1, len(text)) * size * CHAR_ASPECT / 2
            x0, y0, x1, y1 = p.bbox
            outside = lab.side == "outside"
            if side == "left":
                at = (min(x0, body[0] if outside else x0) - edge - half, p.y)
            elif side == "right":
                at = (max(x1, body[2] if outside else x1) + edge + half, p.y)
            elif side == "up":
                at = (p.x, min(y0, body[1] if outside else y0) - edge - half)
            else:
                at = (p.x, max(y1, body[3] if outside else y1) + edge + half)
            rotation = 0 if side in ("left", "right") else 90
            if _hits(text_box(at[0], at[1], text, size, rotation), boxes):
                skip(p.number, f"no clear spot for '{text}' beside pad {p.number}",
                     "place it yourself as a text graphic")
                continue
            fp.texts.append(Text(text, round(at[0], 4), round(at[1], 4), rotation, SILK, size))
    if errors:
        raise Refused(errors)
