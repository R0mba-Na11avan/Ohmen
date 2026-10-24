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


class _Base(BaseModel):
    package: str = Field(description="the datasheet's package name, e.g. 'SOT-23-5 (DBV)'")
    drawing: str = Field("", description="which drawing/variant was used, e.g. 'DBV0005A, p.31'")
    name: str = Field("", description="footprint name; leave empty for a generated one")


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


class CustomPad(BaseModel):
    number: str
    x: float
    y: float
    width: float
    height: float
    shape: Literal["roundrect", "rect", "circle", "oval"] = "roundrect"
    drill: float | None = Field(None, description="hole diameter for a through-hole pad")


class Custom(_Base):
    """Anything else: the land pattern itself, read off the datasheet's
    recommended land pattern drawing (pad centres in mm, +y down, origin at the
    package centre)."""

    family: Literal["custom"]
    pads: list[CustomPad]
    body_x: float
    body_y: float
    source: str = Field(description="the recommended land pattern's page")
    polarized: bool | None = Field(None, description="mark pin 1; default: when there are more than two pads")


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
        return len(spec.pads)
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
        numbers = [p.number for p in spec.pads]
        if not numbers:
            errors.append("no pads")
        for p in spec.pads:
            if p.width <= 0 or p.height <= 0:
                errors.append(f"pad {p.number}: size must be positive")
            if p.drill is not None and p.drill >= min(p.width, p.height):
                errors.append(f"pad {p.number}: drill doesn't leave copper around the hole")
        for i, a in enumerate(spec.pads):
            for b in spec.pads[i + 1:]:
                if abs(a.x - b.x) * 2 < a.width + b.width and abs(a.y - b.y) * 2 < a.height + b.height \
                        and a.number != b.number:
                    errors.append(f"pads {a.number} and {b.number} overlap")
    if not math.isfinite(sum(d.mid for d in dims(spec).values())):
        errors.append("a dimension isn't a number")
    return errors, warnings
