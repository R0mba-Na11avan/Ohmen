"""IPC-7351B land patterns at the nominal (B) density level.

For leads with a span L (toe to toe), foot length T and width W:

  Z = Lmin + 2·Jt + √(CL² + F² + P²)     outer edge to outer edge of the lands
  G = Smax − 2·Jh − √(CS² + F² + P²)     inner edge to inner edge (S = L − 2T)
  X = Wmin + 2·Js + √(CW² + F² + P²)     land width

C is a dimension's tolerance (max − min); S's comes from L's and T's by RMS, as
the standard does. F and P are board fabrication and placement tolerances. Z
and G round to 0.05 mm, X to 0.01 mm. Checked against KiCad's own library in
tests/test_footprints.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pcb_assistant.footprints.spec import Dim

F = 0.10  # board fabrication tolerance
P = 0.05  # placement tolerance
COURTYARD = 0.25  # excess around pads and body at density level B


@dataclass(frozen=True)
class Fillets:
    toe: float
    heel: float
    side: float


GULLWING = Fillets(0.35, 0.35, 0.03)  # pitch over 0.625 mm
GULLWING_FINE = Fillets(0.35, 0.35, -0.02)  # pitch 0.625 mm and under
NO_LEAD = Fillets(0.30, 0.00, -0.02)  # QFN / DFN / SON; side as KiCad's library has it (IPC: -0.04)
CHIP = Fillets(0.35, -0.05, 0.00)  # 1608 metric (0603) and up
CHIP_SMALL = Fillets(0.20, -0.05, 0.00)  # under 1608

MIN_LAND_GAP = 0.2  # copper between neighbouring lands


def gullwing_fillets(pitch: float) -> Fillets:
    return GULLWING if pitch > 0.625 else GULLWING_FINE


def chip_fillets(body_length: Dim) -> Fillets:
    return CHIP if body_length.mid >= 1.5 else CHIP_SMALL


def _rss(*c: float) -> float:
    return math.sqrt(sum(x * x for x in c))


def _round(v: float, step: float) -> float:
    return round(round(v / step) * step, 4)


@dataclass(frozen=True)
class Land:
    """One row pair of lands: Z/G/X as above; `length` and `center` (distance
    from the package centre to each land's centre) follow from them."""

    z: float
    g: float
    x: float

    @property
    def length(self) -> float:
        return round((self.z - self.g) / 2, 4)

    @property
    def center(self) -> float:
        return round((self.z + self.g) / 4, 4)


def land(span: Dim, foot: Dim, width: Dim, j: Fillets, pitch: float | None = None) -> Land:
    """Lands for leads spanning `span` toe to toe, `foot` long, `width` wide."""
    cl, ct, cw = span.tol, foot.tol, width.tol
    s_min = span.lo - 2 * foot.hi
    s_max = span.hi - 2 * foot.lo
    cs = _rss(cl, ct, ct)  # RMS tolerance of S, instead of the worst case s_max - s_min
    s_max_rms = (s_min + s_max) / 2 + cs / 2
    z = span.lo + 2 * j.toe + _rss(cl, F, P)
    g = s_max_rms - 2 * j.heel - _rss(cs, F, P)
    x = width.lo + 2 * j.side + _rss(cw, F, P)
    if pitch is not None:
        x = min(x, pitch - MIN_LAND_GAP)
    return Land(_round(z, 0.05), _round(max(g, 0.0), 0.05), _round(x, 0.01))


def tht_drill(lead: Dim) -> float:
    """Finished hole for a through-hole lead (B: largest lead + 0.2 mm), up to 0.05."""
    return round(math.ceil((lead.hi + 0.2) / 0.05 - 1e-9) * 0.05, 4)


def tht_pad(drill: float, pitch: float) -> float:
    """Round/square pad: a 0.3 mm annular ring, kept 0.25 mm clear of the next pad."""
    return _round(min(drill + 0.6, pitch - 0.25), 0.05)
