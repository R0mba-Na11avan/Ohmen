"""Persisted per-project BOM store (`.pcb_assistant/bom.json`)."""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pcb_assistant.bom.match import candidates, mpn_related, mpns_conflict
from pcb_assistant.bom.models import BomRow, schematic_row_id
from pcb_assistant.ir.models import Design
from pcb_assistant.jsonio import lock_for, read_json, write_json

DEFAULT_REL = Path(".pcb_assistant") / "bom.json"
# Approved distributor facts survive a re-fill from the schematic.
DISTRIBUTOR_FIELDS = ("manufacturer", "distributor_pn", "stock", "lifecycle", "fetched_at", "min_order")


def refdes_key(ref: str) -> tuple:
    """Natural sort key: "R10" -> ("r", 10, ""), so R2 sorts before R10."""
    parts = re.split(r"(\d+)", ref.lower())
    return tuple(int(p) if p.isdigit() else p for p in parts)


# Facts a planned row carries over when it merges into the schematic row.
CARRIED_FIELDS = ("datasheet_url", "product_url", "unit_price", *DISTRIBUTOR_FIELDS)


@dataclass
class Reconciled:
    """What reconcile() did: merges (planned id, schematic id), ambiguous planned
    rows (id -> candidate ids), and planned rows with no schematic match."""

    merged: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    unplaced: list[str] = field(default_factory=list)


class BomStore:
    """The working BOM for one project.

    Every edit re-reads the file under a per-file lock and saves at the end, so
    an agent turn and a click in the BOM tab never overwrite each other's rows.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._rows: dict[str, BomRow] = {}
        self._depth = 0
        self.last_reconcile = Reconciled()
        self._fresh()

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "BomStore":
        return cls(Path(project_dir) / DEFAULT_REL)

    def _load(self) -> None:
        data = read_json(self.path) or {}
        rows = {}
        for d in data.get("rows", []):
            row = BomRow.model_validate(d)
            rows[row.id] = row
        self._rows = rows

    def _save(self) -> None:
        write_json(self.path, {"rows": [r.model_dump() for r in self._rows.values()]})

    @contextmanager
    def _edit(self) -> Iterator[None]:
        """Fresh rows in, saved rows out. Nested edits share the outer one."""
        with lock_for(self.path):
            outer = self._depth == 0
            if outer:
                self._load()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            if outer:
                self._save()

    def _fresh(self) -> None:
        if self._depth == 0:
            with lock_for(self.path):
                self._load()

    def list(self) -> list[BomRow]:
        """Rows ordered by first refdes (natural: R2 before R10); rows without refdes last."""
        self._fresh()
        return sorted(
            self._rows.values(),
            key=lambda r: (not r.refdes, refdes_key(r.refdes[0]) if r.refdes else ()),
        )

    def get(self, row_id: str) -> BomRow | None:
        self._fresh()
        return self._rows.get(row_id)

    def add(self, row: BomRow) -> BomRow:
        if not row.id:
            row = row.model_copy(update={"id": uuid.uuid4().hex[:12]})
        with self._edit():
            self._rows[row.id] = row
        return row

    def update(self, row_id: str, **fields) -> BomRow:
        with self._edit():
            row = self._rows.get(row_id)
            if row is None:
                raise KeyError(row_id)
            updated = row.model_copy(update=fields)
            self._rows[row_id] = updated
        return updated

    def remove(self, row_id: str) -> None:
        with self._edit():
            self._rows.pop(row_id, None)

    def fill_from_design(self, design: Design) -> int:
        """Group placed components by (value, footprint) into schematic rows.

        Idempotent: re-running updates the same rows (stable ids) and refreshes
        refdes/qty. Leaves llm/manual rows and any user pin/reject status intact.
        A schematic row whose (value, footprint) no longer exists is dropped — a
        part moved to a new footprint lands in its new row, not in both.
        Parts marked "Exclude from BOM" or "Do not populate" in KiCad are skipped.
        Then reconciles: planned rows the schematic now places merge in.
        Returns the number of schematic rows after the fill.
        """
        with self._edit():
            groups: dict[str, list] = {}
            for c in design.components:
                if not c.in_bom or c.dnp:
                    continue
                groups.setdefault(schematic_row_id(c.value, c.footprint), []).append(c)
            for rid, comps in list(groups.items()):
                # Same value and footprint but different MPNs: different parts, one row each.
                if len({c.mpn for c in comps if c.mpn}) > 1:
                    del groups[rid]
                    for c in comps:
                        groups.setdefault(schematic_row_id(c.value, c.footprint, c.mpn), []).append(c)

            for rid in [rid for rid, r in self._rows.items() if r.source == "schematic" and rid not in groups]:
                del self._rows[rid]

            count = 0
            for rid, comps in groups.items():
                comps_sorted = sorted(comps, key=lambda c: refdes_key(c.ref))
                first = comps_sorted[0]
                mpns = {c.mpn for c in comps_sorted if c.mpn}
                existing = self._rows.get(rid)
                mpn = next(iter(mpns)) if len(mpns) == 1 else None
                if existing and existing.mpn and (mpn is None or mpn_related(mpn, existing.mpn)):
                    # Keep a merged-in variant ('...-N16R8') over the schematic's base name.
                    mpn = existing.mpn if mpn is None or len(existing.mpn) > len(mpn) else mpn
                kept = (
                    existing.model_dump(include=set(DISTRIBUTOR_FIELDS)) if existing else {}
                )
                row = BomRow(
                    **kept,
                    id=rid,
                    refdes=[c.ref for c in comps_sorted],
                    qty=len(comps_sorted),
                    value=first.value,
                    footprint=first.footprint,
                    mpn=mpn,
                    datasheet_url=first.datasheet or (existing.datasheet_url if existing else None),
                    product_url=(existing.product_url if existing else None),
                    unit_price=(existing.unit_price if existing else None),
                    why=(existing.why if existing else "from schematic"),
                    source="schematic",
                    status=(existing.status if existing else "active"),
                )
                self._rows[rid] = row
                count += 1

            self.last_reconcile = self.reconcile()
        return count

    def reconcile(self) -> Reconciled:
        """Merge each planned (llm/manual) row into the schematic row it matches.

        A sure match merges; an unsure one (several candidates, or a different
        MPN) tags the row `duplicate_of` for a person or the agent to settle with
        merge() or keep_separate(); none leaves it planned. Rejected rows are
        left alone.
        """
        with self._edit():
            out = Reconciled()
            placed = [r.id for r in self._rows.values() if r.source == "schematic"]
            planned = [r for r in self._rows.values() if r.source != "schematic"]
            for row in planned:
                if row.status == "rejected":
                    self._rows[row.id] = row.model_copy(update={"duplicate_of": []})
                    continue
                pool = [self._rows[i] for i in placed if i not in row.separate_from]
                hits, sure = candidates(row, pool)
                if sure:
                    self._absorb(row, self._rows[hits[0].id])
                    out.merged.append((row.id, hits[0].id))
                    continue
                ids = [h.id for h in hits]
                self._rows[row.id] = row.model_copy(update={"duplicate_of": ids})
                if ids:
                    out.ambiguous[row.id] = ids
                else:
                    out.unplaced.append(row.id)
        return out

    def merge(self, source_id: str, into_id: str) -> BomRow:
        """Fold row `source_id` into `into_id` and delete it. The kept row keeps
        its refdes/qty/value/footprint and takes over the source's part facts
        where it has none. A schematic row can't be the source: the next fill
        would bring it back."""
        with self._edit():
            src, into = self._rows.get(source_id), self._rows.get(into_id)
            if src is None or into is None:
                raise KeyError(source_id if src is None else into_id)
            if source_id == into_id:
                raise ValueError("can't merge a row into itself")
            if src.source == "schematic":
                raise ValueError(
                    "schematic rows are rebuilt from the schematic; merge the other row into this one"
                )
            merged = self._absorb(src, into)
        return merged

    def keep_separate(self, row_id: str) -> BomRow:
        """A person (or the agent) says this planned row is none of its
        `duplicate_of` candidates; reconcile won't suggest them again."""
        with self._edit():
            row = self._rows.get(row_id)
            if row is None:
                raise KeyError(row_id)
            return self.update(
                row_id, separate_from=sorted({*row.separate_from, *row.duplicate_of}), duplicate_of=[]
            )

    def _absorb(self, src: BomRow, into: BomRow) -> BomRow:
        fields: dict = {}
        why = src.why
        if mpns_conflict(src, into):
            # A different part was planned; its prices and DigiKey facts aren't this one's.
            why = f"{why} (planned as {src.mpn})".strip()
        else:
            fields = {f: getattr(src, f) for f in CARRIED_FIELDS if getattr(into, f) is None}
            if src.mpn and (not into.mpn or len(src.mpn) > len(into.mpn)):
                fields["mpn"] = src.mpn  # the planned row names the exact variant
        if why and (not into.why or into.why == "from schematic"):
            fields["why"] = why
        if into.status == "active":
            fields["status"] = src.status  # a pin or reject the planned row carried
        if into.source != "schematic":
            fields["qty"] = max(into.qty, src.qty)
        fields["duplicate_of"] = []
        del self._rows[src.id]
        self._rows[into.id] = merged = into.model_copy(update=fields)
        return merged
