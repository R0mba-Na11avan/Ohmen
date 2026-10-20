"""Which parts need a datasheet, and what to call each one.

A part's key is its manufacturer part number when we can find one: the `mpn`
field, else the schematic Datasheet field when it holds a bare part number
(e.g. "SMBJ30A"), else the value when it looks like a part number. Passives and
generic library symbols ("LED", "D_Schottky", "USB_C_Receptacle_...") have no
single datasheet and are skipped. Refs sharing a key are grouped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pcb_assistant.ir.models import Design

# 100n, 4.7k, 10uF, 1μ, 0R, 2k2, 15uH, 0.1 — a value, not a part number.
_PASSIVE_VALUE = re.compile(
    r"^\d+([.,]\d+)?\s*[pnuµμmkKMRrGF]?[0-9]*\s*(F|H|Ω|ohms?|R)?$", re.IGNORECASE
)
_PASSIVE_REFS = ("R", "C", "L", "FB", "TP", "H", "MH", "FID", "NT")


@dataclass
class PartRef:
    """One datasheet-worthy part: its key, the refs that use it, any URL hint."""

    key: str
    refs: list[str] = field(default_factory=list)
    url_hint: str | None = None  # the schematic Datasheet field, when it's a URL
    value: str | None = None


def is_url(s: str | None) -> bool:
    return bool(s) and s.strip().lower().startswith(("http://", "https://"))


def looks_like_mpn(s: str | None) -> bool:
    """Heuristic: has letters and digits, no spaces/underscores, not a value."""
    if not s:
        return False
    s = s.strip()
    if len(s) < 4 or " " in s or "_" in s or is_url(s):
        return False
    if not (re.search(r"[A-Za-z]", s) and re.search(r"\d", s)):
        return False
    return not _PASSIVE_VALUE.match(s)


def _ref_prefix(ref: str) -> str:
    m = re.match(r"[A-Za-z#]+", ref)
    return m.group(0).upper() if m else ""


def base_mpn(key: str) -> str:
    """The family core of a part number, for matching it inside a datasheet.

    TJA1051TK-3 -> TJA1051, LMR51625XDDCR -> LMR51625, 74AHCT1G125 -> 74AHCT1G125,
    AP2112K-3.3TRG1 -> AP2112, ESP32-S3-WROOM-1 -> ESP32-S3-WROOM-1 (no clean core).
    """
    k = key.upper().strip()
    # Logic families keep their whole name: 74AHCT1G125, 74HC595.
    m = re.match(r"^(SN)?(74[A-Z]+\d+(?:[A-Z]\d+)?)", k)
    if m:
        return m.group(2)
    m = re.match(r"^([A-Z]{2,})(\d{3,})", k)  # TJA1051, LMR51625, AP2112, NUP2105
    if m:
        return m.group(1) + m.group(2)
    return k


def _key_for(component) -> str | None:
    if looks_like_mpn(component.mpn):
        return component.mpn.strip()
    ds = component.datasheet or component.fields.get("Datasheet")
    if ds and not is_url(ds) and looks_like_mpn(ds):
        return ds.strip()
    if _ref_prefix(component.ref) in _PASSIVE_REFS:
        return None
    if looks_like_mpn(component.value):
        return component.value.strip()
    return None


def parts_needing_datasheets(design: Design) -> list[PartRef]:
    """Every distinct part on the board that should have a datasheet."""
    parts: dict[str, PartRef] = {}
    for c in design.components:
        if c.ref.startswith("#"):
            continue
        key = _key_for(c)
        if key is None:
            continue
        p = parts.setdefault(key, PartRef(key=key, value=c.value))
        p.refs.append(c.ref)
        ds = c.datasheet or c.fields.get("Datasheet")
        if p.url_hint is None and is_url(ds):
            p.url_hint = ds.strip()
    return sorted(parts.values(), key=lambda p: p.refs[0])


def part_for(design: Design | None, name: str) -> PartRef | None:
    """Resolve what the agent typed — a ref ('U3'), a key, or a base MPN — to a part."""
    if design is None:
        return None
    parts = parts_needing_datasheets(design)
    n = name.strip().upper()
    for p in parts:
        if n == p.key.upper() or n in (r.upper() for r in p.refs):
            return p
    for p in parts:
        if n == base_mpn(p.key) or base_mpn(n) == base_mpn(p.key):
            return p
    return None
