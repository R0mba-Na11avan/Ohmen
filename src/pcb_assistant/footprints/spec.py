"""What the model reads off a package drawing, as a typed spec.

Every dimension is a `Dim` in millimetres — min/max as the drawing's table
gives them (or a nominal alone) — with the page it came from, so the engineer
can check each number against the datasheet before accepting the footprint.
`check` cross-checks the numbers (leads fit along the body, the span is wider
than the body, …) and says what's wrong in words the model can act on.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

ASSUMED_TOL = 0.05  # ± applied when a drawing gives a nominal only


class Dim(BaseModel):
    """A dimension in mm: min and max from the drawing's table, or nom alone."""

    min: float | None = None
    nom: float | None = None
    max: float | None = None
    source: str = Field("", description="where it's from, e.g. 'TPS7A20 datasheet p.31'")

    @property
    def lo(self) -> float:
        if self.min is not None:
            return self.min
        return (self.nom if self.nom is not None else self.max or 0.0) - (ASSUMED_TOL if self.max is None else 0)

    @property
    def hi(self) -> float:
        if self.max is not None:
            return self.max
        return (self.nom if self.nom is not None else self.min or 0.0) + (ASSUMED_TOL if self.min is None else 0)

    @property
    def mid(self) -> float:
        return self.nom if self.nom is not None else (self.lo + self.hi) / 2

    @property
    def tol(self) -> float:
        return self.hi - self.lo


class Refused(ValueError):
    """The spec asks for something the builder won't draw; each error says why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


Point = tuple[float, float]
GraphicLayer = Literal["F.SilkS", "F.Fab", "Cmts.User"]


class _Graphic(BaseModel):
    layer: GraphicLayer = "F.SilkS"
    width: float | None = Field(None, description="stroke width; default: the layer's usual")


class GLine(_Graphic):
    type: Literal["line"]
    start: Point
    end: Point


class GArc(_Graphic):
    type: Literal["arc"]
    start: Point
    mid: Point = Field(description="any point on the arc between its ends")
    end: Point


class GCircle(_Graphic):
    type: Literal["circle"]
    center: Point
    radius: float
    fill: bool = False


class GPolygon(_Graphic):
    type: Literal["polygon"]
    points: list[Point]
    fill: bool = False


class GText(_Graphic):
    type: Literal["text"]
    at: Point = Field(description="the text's centre")
    text: str
    size: float = 1.0
    rotation: Literal[0, 90, 180, 270] = 0


Graphic = Annotated[Union[GLine, GArc, GCircle, GPolygon, GText], Field(discriminator="type")]


class Silk(BaseModel):
    """How the automatic silkscreen is drawn."""

    outline: Literal["box", "corners", "none"] = "box"
    pin1: Literal["auto", "triangle", "dot", "line", "none"] = Field(
        "auto", description="auto: a triangle on a polarised part, unless polarity marks it")
    polarity: Literal["none", "cathode_bar", "plus"] = Field(
        "none", description="at pad 1: a bar across that end (diodes), or a + (electrolytics)")


class Labels(BaseModel):
    """Text beside pads, placed clear of copper."""

    text: dict[str, str] = Field(default_factory=dict, description="pad number -> label, e.g. {'1': '+'}")
    from_symbol: bool = Field(False, description="label every pad with its pin name from the spec's symbol")
    auto: dict[str, str] = Field(default_factory=dict, description="filled in by Ohmen from the symbol; "
                                                                   "one with no room is left out, not refused")
    size: float | None = Field(None, description="text height; default: what fits the pitch, up to 1 mm")
    side: Literal["outside", "inside"] = "outside"


class Cutout(BaseModel):
    """A hole milled through the board under the part (Edge.Cuts)."""

    points: list[Point]
    closed: bool = Field(True, description="false for a notch: its two ends must meet the board's own outline")
    source: str = Field(description="where the cutout is dimensioned: datasheet page, drawing, or the engineer")


class Keepout(BaseModel):
    """An area the layout must keep copper out of (under an antenna, say)."""

    points: list[Point]
    layers: Literal["front", "back", "all"] = "all"
    tracks: bool = Field(True, description="tracks not allowed")
    vias: bool = Field(True, description="vias not allowed")
    copper_pour: bool = Field(True, description="copper pour not allowed")
    source: str = Field(description="where the keepout is asked for: datasheet page, drawing, or the engineer")


class _Base(BaseModel):
    package: str = Field(description="the datasheet's package name, e.g. 'SOT-23-5 (DBV)'")
    drawing: str = Field("", description="which drawing/variant was used, e.g. 'DBV0005A, p.31'")
    name: str = Field("", description="footprint name; leave empty for a generated one")
    symbol: str = Field("", description="the symbol this footprint is for: a refdes on the schematic "
                                        "('U3') or a library symbol ('MyLib:ESP32-MOD'); pads are "
                                        "checked against its pins")
    silk: Silk = Field(default_factory=Silk)
    graphics: list[Graphic] = Field(default_factory=list)
    labels: Labels | None = None
    body_outline: list[Point] = Field(default_factory=list, description="the body as a polygon, when it "
                                                                         "isn't a plain rectangle")
    cutouts: list[Cutout] = Field(default_factory=list)
    keepouts: list[Keepout] = Field(default_factory=list)


class DualGullwing(_Base):
    """SOIC / SOP / TSSOP / MSOP / SOT-23 family: two rows of gull-wing leads,
    pin 1 top-left, numbered counter-clockwise."""

    family: Literal["dual_gullwing"]
    positions: int = Field(description="lead positions on both sides together (SOT-23: 6)")
    skip_positions: list[int] = Field(
        default_factory=list,
        description="positions with no lead, counted CCW from top-left; the rest are numbered "
                    "1..n in order (SOT-23: [2,4,6]; SOT-23-5: [5])")
    pitch: float
    lead_span: Dim = Field(description="toe to toe across the two rows (E / HE)")
    lead_length: Dim = Field(description="foot length in contact with the board (L / Lp)")
    lead_width: Dim = Field(description="b")
    body_width: Dim = Field(description="across the rows, without leads (E1)")
    body_length: Dim = Field(description="along the rows (D)")
    exposed_pad: tuple[Dim, Dim] | None = Field(None, description="thermal pad (across, along), if any")


class QuadGullwing(_Base):
    """QFP / LQFP / TQFP: gull-wing leads on four sides, pin 1 at the top of the
    left side, numbered counter-clockwise."""

    family: Literal["quad_gullwing"]
    pins: int
    pitch: float
    lead_span_x: Dim = Field(description="toe to toe, left to right")
    lead_span_y: Dim = Field(description="toe to toe, top to bottom")
    lead_length: Dim
    lead_width: Dim
    body_x: Dim
    body_y: Dim
    exposed_pad: tuple[Dim, Dim] | None = None


class DualNoLead(_Base):
    """DFN / SON / WSON: terminals under two body edges, pin 1 top-left, CCW."""

    family: Literal["dual_nolead"]
    pins: int
    pitch: float
    body_width: Dim = Field(description="across the terminal rows (E)")
    body_length: Dim = Field(description="along the rows (D)")
    terminal_length: Dim = Field(description="L, measured in from the body edge")
    terminal_width: Dim = Field(description="b")
    exposed_pad: tuple[Dim, Dim] | None = Field(None, description="(across, along) = (E2, D2)")


class QuadNoLead(_Base):
    """QFN / VQFN / WQFN: terminals under four body edges, pin 1 at the top of
    the left side, CCW."""

    family: Literal["quad_nolead"]
    pins: int
    pitch: float
    body_x: Dim
    body_y: Dim
    terminal_length: Dim
    terminal_width: Dim
    exposed_pad: tuple[Dim, Dim] | None = Field(None, description="(x, y) = (E2, D2)")


class Chip(_Base):
    """Two-terminal SMD chip: resistor, capacitor, inductor, chip diode/LED/fuse."""

    family: Literal["chip"]
    kind: Literal["resistor", "capacitor", "inductor", "diode", "led", "fuse", "other"] = "other"
    body_length: Dim = Field(description="terminal to terminal (L)")
    body_width: Dim = Field(description="W")
    terminal_length: Dim = Field(description="each end's metallisation (T / L1)")


class PinHeader(_Base):
    """Through-hole header or connector on a regular grid; pin 1 top-left,
    numbered across each row (1,2 on the first row for a 2-row header)."""

    family: Literal["pin_header"]
    rows: int
    pins_per_row: int
    pitch: float = Field(description="along a row")
    row_pitch: float = Field(0.0, description="between rows; 0 = same as pitch")
    lead_diameter: Dim = Field(description="round lead diameter, or a square lead's diagonal")


Drill = Union[float, tuple[float, float]]  # round, or a slot (width, height) in the pad's own frame
Corner = Literal["top_left", "top_right", "bottom_left", "bottom_right"]
_STEP = {"right": (1, 0), "left": (-1, 0), "down": (0, 1), "up": (0, -1)}


class _Land(BaseModel):
    width: float
    height: float
    shape: Literal["roundrect", "rect", "circle", "oval", "chamfered_rect"] = "roundrect"
    drill: Drill | None = Field(None, description="hole for a through-hole pad: a diameter, or "
                                                  "[width, height] for a slot")
    rotation: float = Field(0.0, description="degrees, counter-clockwise; width/height/slot are the "
                                             "unrotated pad's")
    paste: bool = Field(True, description="solder paste on an SMD pad (off for a shield or test pad)")
    mask: bool = Field(True, description="solder mask opening")
    chamfer: list[Corner] = Field(default_factory=list, description="cut corners of a chamfered_rect")
    chamfer_ratio: float = 0.2


class CustomPad(_Land):
    number: str
    x: float
    y: float


class PadRow(_Land):
    """A straight run of identical pads: the first pad's centre, then count
    pads a pitch apart in a direction."""

    x: float
    y: float
    count: int = Field(0, description="pads in the row; taken from numbers when those are given")
    pitch: float
    direction: Literal["right", "left", "down", "up"] = Field(description="from the first pad (+y is down)")
    numbers: list[str] = Field(default_factory=list, description="each pad's number in order, e.g. to "
                                                                 "follow a symbol; else start and step")
    start: int = 1
    step: int = 1

    @property
    def n(self) -> int:
        return len(self.numbers) or self.count

    def pads(self) -> list[CustomPad]:
        dx, dy = _STEP[self.direction]
        land = {k: getattr(self, k) for k in _Land.model_fields}
        return [CustomPad(number=self.numbers[i] if self.numbers else str(self.start + i * self.step),
                          x=round(self.x + dx * i * self.pitch, 4), y=round(self.y + dy * i * self.pitch, 4),
                          **land) for i in range(self.n)]


class Hole(BaseModel):
    """A non-plated hole: a locating peg or a mounting hole."""

    x: float
    y: float
    drill: Drill


class Custom(_Base):
    """Anything else (connectors, modules): the land pattern itself, read off
    the datasheet's recommended land pattern drawing (pad centres in mm, +y
    down, origin at the package centre)."""

    family: Literal["custom"]
    pads: list[CustomPad] = Field(default_factory=list)
    rows: list[PadRow] = Field(default_factory=list)
    holes: list[Hole] = Field(default_factory=list)
    body_x: float
    body_y: float
    body_offset: tuple[float, float] = Field((0.0, 0.0), description="the body's centre, when it isn't "
                                                                     "the origin")
    source: str = Field(description="where the land pattern is from: its datasheet page, an attached "
                                    "drawing, or the engineer")
    polarized: bool | None = Field(None, description="mark pin 1; default: when there are more than two pads")

    def all_pads(self) -> list[CustomPad]:
        return [*self.pads, *(p for row in self.rows for p in row.pads())]


def drill_size(drill: Drill | None) -> tuple[float, float] | None:
    """A drill as (width, height); equal for a round hole."""
    if drill is None:
        return None
    return (drill, drill) if isinstance(drill, (int, float)) else (drill[0], drill[1])


def _box(x: float, y: float, w: float, h: float, rotation: float = 0.0):
    if rotation % 180:
        a = math.radians(rotation)
        c, s = abs(math.cos(a)), abs(math.sin(a))
        w, h = w * c + h * s, w * s + h * c
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _on_pad(x: float, y: float, r: float, pad) -> bool:
    """Whether a hole of radius r at (x, y) touches the pad, however the pad is turned."""
    a = math.radians(pad.rotation)
    dx, dy = x - pad.x, y - pad.y
    u, v = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)  # in the pad's own frame
    return abs(u) < pad.width / 2 + r - 1e-9 and abs(v) < pad.height / 2 + r - 1e-9


def _overlap(a, b) -> bool:
    return a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9 and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9


FootprintSpec = Annotated[
    Union[DualGullwing, QuadGullwing, DualNoLead, QuadNoLead, Chip, PinHeader, Custom],
    Field(discriminator="family"),
]


def dims(spec) -> dict[str, Dim]:
    """Every Dim in the spec by field name (exposed pads as name.0 / name.1)."""
    out: dict[str, Dim] = {}
    for k in type(spec).model_fields:
        v = getattr(spec, k)
        if isinstance(v, Dim):
            out[k] = v
        elif isinstance(v, tuple) and v and all(isinstance(d, Dim) for d in v):
            for i, d in enumerate(v):
                out[f"{k}.{i}"] = d
    return out


def lead_count(spec) -> int:
    if isinstance(spec, DualGullwing):
        return spec.positions - len(set(spec.skip_positions))
    if isinstance(spec, PinHeader):
        return spec.rows * spec.pins_per_row
    if isinstance(spec, Chip):
        return 2
    if isinstance(spec, Custom):
        return len(spec.all_pads())
    return spec.pins


def check(spec) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors mean the numbers can't be a real package —
    re-read the drawing; warnings are worth a second look."""
    errors: list[str] = []
    warnings: list[str] = []
    for name, d in dims(spec).items():
        if d.min is None and d.nom is None and d.max is None:
            errors.append(f"{name}: no value")
            continue
        if any(v is not None and v <= 0 for v in (d.min, d.nom, d.max)):
            errors.append(f"{name}: must be positive")
        if d.lo > d.hi or (d.nom is not None and not d.lo <= d.nom <= d.hi):
            errors.append(f"{name}: min ≤ nom ≤ max is broken ({d.min}, {d.nom}, {d.max})")
        if d.min is None and d.max is None:
            warnings.append(f"{name}: drawing gives a nominal only; assumed ±{ASSUMED_TOL} mm")
        if not d.source:
            warnings.append(f"{name}: no datasheet page cited")
    if errors:
        return errors, warnings

    def fits(n_per_row: int, pitch: float, width: Dim, length: Dim, what: str) -> None:
        if width.hi >= pitch:
            errors.append(f"lead width {width.hi} mm doesn't fit a {pitch} mm pitch")
        row = pitch * (n_per_row - 1) + width.lo
        if row > length.hi + 0.2:
            errors.append(f"{n_per_row} leads at {pitch} mm span {row:.2f} mm, longer than the {what} "
                          f"({length.hi} mm): check the pitch and pin count")

    def pad_inside(pad: tuple[Dim, Dim] | None, bx: Dim, by: Dim) -> None:
        if pad and (pad[0].hi >= bx.lo or pad[1].hi >= by.lo):
            errors.append("exposed pad is as big as the body: check E2/D2")

    if isinstance(spec, DualGullwing):
        n = spec.positions
        if n % 2 or n < 2:
            errors.append(f"positions must be even (both sides), got {n}")
        if any(not 1 <= p <= n for p in spec.skip_positions):
            errors.append(f"skip_positions must be within 1..{n}")
        fits(n // 2, spec.pitch, spec.lead_width, spec.body_length, "body")
        if spec.lead_span.lo <= spec.body_width.hi:
            errors.append("lead span must be wider than the body (toe to toe vs. body width)")
        if spec.lead_span.lo - 2 * spec.lead_length.hi <= 0:
            errors.append("feet longer than half the span: is lead_length the foot, not the whole lead?")
        pad_inside(spec.exposed_pad, spec.body_width, spec.body_length)
    elif isinstance(spec, QuadGullwing):
        if spec.pins % 4 or spec.pins < 8:
            errors.append(f"a quad package has a multiple of 4 pins, got {spec.pins}")
        per = spec.pins // 4
        fits(per, spec.pitch, spec.lead_width, spec.body_y, "body side")
        fits(per, spec.pitch, spec.lead_width, spec.body_x, "body side")
        if spec.lead_span_x.lo <= spec.body_x.hi or spec.lead_span_y.lo <= spec.body_y.hi:
            errors.append("lead span must be wider than the body on both axes")
        pad_inside(spec.exposed_pad, spec.body_x, spec.body_y)
    elif isinstance(spec, DualNoLead):
        if spec.pins % 2:
            errors.append(f"a dual package has an even pin count, got {spec.pins}")
        fits(spec.pins // 2, spec.pitch, spec.terminal_width, spec.body_length, "body")
        if 2 * spec.terminal_length.hi >= spec.body_width.lo:
            errors.append("terminals meet in the middle: check terminal_length (L) and body width (E)")
        pad_inside(spec.exposed_pad, spec.body_width, spec.body_length)
        if spec.exposed_pad and spec.exposed_pad[0].hi > spec.body_width.lo - 2 * spec.terminal_length.lo:
            warnings.append("exposed pad reaches the terminals' inner ends: check E2 against L")
    elif isinstance(spec, QuadNoLead):
        if spec.pins % 4:
            errors.append(f"a quad package has a multiple of 4 pins, got {spec.pins}")
        per = spec.pins // 4
        fits(per, spec.pitch, spec.terminal_width, spec.body_y, "body side")
        fits(per, spec.pitch, spec.terminal_width, spec.body_x, "body side")
        pad_inside(spec.exposed_pad, spec.body_x, spec.body_y)
        if spec.exposed_pad and spec.exposed_pad[0].hi > spec.body_x.lo - 2 * spec.terminal_length.lo:
            warnings.append("exposed pad reaches the terminals' inner ends: check E2 against L")
    elif isinstance(spec, Chip):
        if 2 * spec.terminal_length.hi >= spec.body_length.lo:
            errors.append("terminals meet in the middle: check terminal_length against body_length")
    elif isinstance(spec, PinHeader):
        if spec.rows < 1 or spec.pins_per_row < 1:
            errors.append("rows and pins_per_row must be at least 1")
        if spec.lead_diameter.hi >= min(spec.pitch, spec.row_pitch or spec.pitch):
            errors.append("leads as thick as the pitch: check lead_diameter (a square post's diagonal)")
    elif isinstance(spec, Custom):
        for i, row in enumerate(spec.rows, 1):
            if row.n < 1:
                errors.append(f"row {i}: give count or numbers")
            if row.numbers and row.count and row.count != len(row.numbers):
                errors.append(f"row {i}: count is {row.count} but {len(row.numbers)} numbers are given")
            if row.pitch <= 0:
                errors.append(f"row {i}: pitch must be positive")
        pads = spec.all_pads()
        if not pads:
            errors.append("no pads")
        for p in pads:
            if not p.number.strip():  # an unnumbered pad is a paste window to every later check
                errors.append(f"pad at ({p.x}, {p.y}): give it a number (shield and mounting pads can share one)")
            if p.width <= 0 or p.height <= 0:
                errors.append(f"pad {p.number}: size must be positive")
            if p.shape == "circle" and abs(p.width - p.height) > 1e-9:
                errors.append(f"pad {p.number}: a circle has one diameter, so width and height must match "
                              "(oval for a longer pad)")
            hole = drill_size(p.drill)
            if hole and (min(hole) <= 0 or hole[0] >= p.width or hole[1] >= p.height):
                errors.append(f"pad {p.number}: drill doesn't leave copper around the hole")
            if p.shape == "chamfered_rect" and not p.chamfer:
                errors.append(f"pad {p.number}: a chamfered_rect says which corners in chamfer")
            if not 0 < p.chamfer_ratio <= 0.5:
                errors.append(f"pad {p.number}: chamfer_ratio is between 0 and 0.5")
        square = [p for p in pads if not p.rotation % 90]  # a turned pad's box says too little
        boxes = [_box(p.x, p.y, p.width, p.height, p.rotation) for p in square]
        for i, a in enumerate(square):
            for j in range(i + 1, len(square)):
                if a.number != square[j].number and _overlap(boxes[i], boxes[j]):
                    errors.append(f"pads {a.number} and {square[j].number} overlap")
        for h in spec.holes:
            size = drill_size(h.drill)
            if min(size) <= 0:
                errors.append(f"hole at ({h.x}, {h.y}): drill must be positive")
                continue
            hit = next((p for p in pads if _on_pad(h.x, h.y, max(size) / 2, p)), None)
            if hit:
                errors.append(f"hole at ({h.x}, {h.y}) lands on pad {hit.number}")
    if not math.isfinite(sum(d.mid for d in dims(spec).values())):
        errors.append("a dimension isn't a number")
    return errors, warnings
