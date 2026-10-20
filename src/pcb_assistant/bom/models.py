"""The BOM row model."""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, computed_field

RowSource = Literal["schematic", "llm", "manual"]
RowStatus = Literal["active", "pinned", "rejected"]


class BomRow(BaseModel):
    """One line of the bill of materials.

    A schematic-sourced row groups every placed part that shares a (value,
    footprint) — so `refdes` is a list and `qty` its length. LLM/manual rows are
    planned parts: once the schematic places one, a fill merges it into the
    schematic row (see bom/match.py). `id` is stable for schematic rows (derived from
    value+footprint) so a re-fill updates in place instead of duplicating.
    """

    id: str
    refdes: list[str] = Field(default_factory=list)
    qty: int = 1
    value: str | None = None
    footprint: str | None = None
    mpn: str | None = None
    datasheet_url: str | None = None
    product_url: str | None = None  # distributor/product page (DigiKey, etc.)
    why: str = ""
    unit_price: float | None = None  # per-unit cost the user fills in
    # Distributor facts — written only when the user approves a DigiKey proposal.
    manufacturer: str | None = None
    distributor_pn: str | None = None  # DigiKey part number, e.g. 296-1234-1-ND
    stock: int | None = None
    lifecycle: str | None = None  # "Active", "Not For New Designs", "Obsolete", ...
    fetched_at: str | None = None  # ISO time the distributor facts were fetched
    min_order: int | None = None  # smallest lot the chosen packaging is sold in
    source: RowSource = "manual"
    status: RowStatus = "active"
    # Planned (llm/manual) rows only: schematic rows this part may already be,
    # when the match was unsure. Empty = unmatched. Settled by merging, or by
    # keeping it separate, which moves the candidates to `separate_from` so
    # they aren't suggested again.
    duplicate_of: list[str] = Field(default_factory=list)
    separate_from: list[str] = Field(default_factory=list)

    @computed_field  # serialized into JSON + JSON-Schema (UI + CSV read it)
    @property
    def line_total(self) -> float | None:
        """What the line costs to order: qty × unit_price, or the whole minimum lot
        when the part isn't sold in fewer (a reel of 3000 costs the reel). None
        when no price is set; rejected rows count 0."""
        if self.unit_price is None or self.status == "rejected":
            return 0.0 if self.status == "rejected" else None
        return round(max(self.qty, self.min_order or 0) * self.unit_price, 4)


def schematic_row_id(value: str | None, footprint: str | None, mpn: str | None = None) -> str:
    """Stable id for a schematic-sourced row keyed on (value, footprint), plus the
    MPN when parts sharing a value and footprint name different parts."""
    key = f"{value or ''}|{footprint or ''}" + (f"|{mpn}" if mpn else "")
    return f"sch:{hashlib.sha1(key.encode()).hexdigest()[:12]}"
