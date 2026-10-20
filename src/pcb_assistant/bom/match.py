"""Decide when a planned BOM row and a schematic row are the same part.

A part the agent (or the engineer) adds before it is placed becomes a second
row once the schematic catches up and the BOM is re-filled. These rules find
the schematic row a planned row now lives in, strongest evidence first:

1. same MPN;
2. one part name extends the other ('ESP32-S3-WROOM-1' placed, '...-N16R8'
   planned), where a schematic row's name is its MPN or a non-numeric value;
3. same value and footprint ('10k' + '0603' matches 10k in R_0603_1608Metric).

`candidates()` returns every schematic row matching at the strongest rule that
matches any, and whether the match is sure. It is sure when there is one hit,
the two rows don't name different MPNs (a planned GRM188 100nF against a placed
C0603C104 100nF is maybe the same capacitor, maybe a deliberate swap), and the
planned name isn't a more specific variant than the placed one (a planned
WS2812B-2020 against a placed WS2812B in a 5050 footprint is a different
package). Unsure matches are left for a person or the agent to settle.
"""
from __future__ import annotations

import math
import re

from pcb_assistant.analysis.values import parse_value
from pcb_assistant.bom.models import BomRow

_MIN_PREFIX = 4  # 'LED' must not swallow 'LED-0603-RED'


def case_size(footprint: str | None) -> str | None:
    """Imperial chip size from a KiCad footprint: 'C_0603_1608Metric' -> '0603'."""
    m = re.search(r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)", footprint or "")
    return m.group(1) if m else None


def norm_mpn(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "").upper()


def mpn_related(a: str | None, b: str | None) -> bool:
    """Equal, or one extends the other by a suffix (variant, packaging)."""
    a, b = norm_mpn(a), norm_mpn(b)
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return a == b or (len(short) >= _MIN_PREFIX and long.startswith(short))


def mpns_conflict(a: BomRow, b: BomRow) -> bool:
    """Both rows name an MPN, and not the same part."""
    return bool(a.mpn and b.mpn) and not mpn_related(a.mpn, b.mpn)


def same_value(a: str | None, b: str | None) -> bool:
    """'10k' == '10K' == '10 kΩ'; '100n' == '100nF'; text compares case-blind."""
    if not a or not b:
        return False
    va, vb = parse_value(a), parse_value(b)
    if va is not None and vb is not None:
        return math.isclose(va, vb, rel_tol=1e-6, abs_tol=1e-15)
    return norm_mpn(a) == norm_mpn(b)


def same_footprint(a: str | None, b: str | None) -> bool:
    """Same footprint name (library prefix ignored), or the same chip size."""
    if not a or not b:
        return False
    na, nb = a.split(":")[-1].lower(), b.split(":")[-1].lower()
    if na == nb:
        return True
    size = case_size(a)
    return size is not None and size == case_size(b)


def _names(row: BomRow) -> list[str]:
    """What a row calls its part: its MPN and any value that isn't a number."""
    out = [row.mpn] if row.mpn else []
    if row.value and parse_value(row.value) is None:
        out.append(row.value)
    return out


def _by_mpn(p: BomRow, r: BomRow) -> bool:
    return bool(p.mpn and r.mpn) and norm_mpn(p.mpn) == norm_mpn(r.mpn)


def _by_name(p: BomRow, r: BomRow) -> bool:
    return any(mpn_related(a, b) for a in _names(p) for b in _names(r))


def _narrows(p: BomRow, r: BomRow) -> bool:
    """The planned name only extends the placed one ('WS2812B-2020' vs 'WS2812B'):
    it picks a variant the schematic doesn't, which may not fit the footprint."""
    ps, rs = [norm_mpn(n) for n in _names(p)], [norm_mpn(n) for n in _names(r)]
    return bool(ps and rs) and not set(ps) & set(rs) and any(
        a.startswith(b) for a in ps for b in rs if mpn_related(a, b)
    )


def _by_value(p: BomRow, r: BomRow) -> bool:
    if not same_value(p.value, r.value):
        return False
    return not p.footprint or same_footprint(p.footprint, r.footprint)


_RULES = (_by_mpn, _by_name, _by_value)


def candidates(planned: BomRow, placed: list[BomRow]) -> tuple[list[BomRow], bool]:
    """(schematic rows `planned` may be, whether it's sure enough to merge)."""
    for rule in _RULES:
        hits = [r for r in placed if rule(planned, r)]
        if hits:
            one = hits[0]
            return hits, len(hits) == 1 and not mpns_conflict(planned, one) and not _narrows(planned, one)
    return [], False
