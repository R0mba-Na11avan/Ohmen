"""BOM fill from DigiKey: proposals the engineer approves row by row.

Nothing here writes the BOM. `propose` looks every row up and stores a
`BomProposal` (what DigiKey offers, why it was chosen, a few alternatives) in
`.pcb_assistant/bom_proposals.json`; only `ProposalStore.approve` copies an
offer onto the row.

How a row is looked up:
  - a part number (MPN field, or a value like AP2112K-3.3TRG1): DigiKey product
    details; a generic number (74AHCT1G125) falls back to a keyword search for
    orderable versions in the footprint's package;
  - a resistor or ceramic capacitor: a keyword search, filtered on the real
    parameters — value, case size, tolerance (1% in feedback dividers), voltage
    rating (at least 2x the highest rail on its pins), dielectric (X7R/X5R/C0G);
  - anything else (connectors, generic LEDs/diodes, switches, inductors whose
    current rating we can't know) is skipped with the reason.

Candidates must be Active and in stock for the row's quantity; the cheapest
buyable packaging at that quantity wins.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

from pcb_assistant.analysis import circuit as cm
from pcb_assistant.analysis.values import fmt, parse_value
from pcb_assistant.bom.match import case_size
from pcb_assistant.bom.models import BomRow
from pcb_assistant.bom.store import BomStore
from pcb_assistant.datasheets.identity import base_mpn, looks_like_mpn
from pcb_assistant.distributors.digikey import DigiKeyClient, DigiKeyError, DigiKeyPart, Variation
from pcb_assistant.ir.models import Design
from pcb_assistant.jsonio import read_json, write_json

PROPOSALS_REL = Path(".pcb_assistant") / "bom_proposals.json"
MAX_ALTERNATIVES = 3
SEARCH_LIMIT = 25
_WORKERS = 4  # DigiKey allows ~120 calls/minute

ProposalStatus = Literal["pending", "approved", "rejected", "skipped"]


class Offer(BaseModel):
    """One orderable part DigiKey has."""

    mpn: str
    manufacturer: str = ""
    description: str = ""
    digikey_pn: str | None = None
    packaging: str = ""  # "Cut Tape (CT)", ...
    unit_price: float | None = None  # at the row's quantity
    stock: int | None = None
    lifecycle: str | None = None
    product_url: str | None = None
    datasheet_url: str | None = None
    min_order: int | None = None  # smallest quantity this packaging is sold in
    min_order_cost: float | None = None  # what that smallest order costs (bulk-only parts)
    bulk_only: bool = False  # can't buy just the row's quantity
    photo_url: str | None = None
    category: str = ""  # DigiKey's category name, e.g. "Chip Resistor - Surface Mount"
    parameters: dict[str, str] = Field(default_factory=dict)  # DigiKey's spec table, for comparing offers


class BomProposal(BaseModel):
    id: str  # = the BOM row id
    refdes: list[str] = Field(default_factory=list)
    qty: int = 1
    value: str | None = None
    footprint: str | None = None
    current_mpn: str | None = None
    current_price: float | None = None
    offer: Offer | None = None
    alternatives: list[Offer] = Field(default_factory=list)
    rationale: str = ""  # how the offer was chosen, or why the row was skipped
    status: ProposalStatus = "pending"
    created_at: str = ""


# --- parsing DigiKey parameter text ---

_DK_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*([pnuµμmkKMG]?)")
_MULT = {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
         "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}


def dk_number(text: str | None) -> float | None:
    """'10 kOhms' -> 10000, '0.1 µF' -> 1e-7, '50V' -> 50, '±1%' -> 1."""
    if not text:
        return None
    m = _DK_NUM.search(text)
    if not m:
        return None
    return float(m.group(1)) * _MULT[m.group(2)]


def _param(part: DigiKeyPart, *names: str) -> str:
    for n in names:
        if n in part.parameters:
            return part.parameters[n]
    return ""


def package_hint(footprint: str | None) -> str | None:
    """A package name DigiKey also uses: 'SOT-23-5', 'SOT-23', 'SOIC-8', ..."""
    m = re.search(r"(SOT-?23-?\d?|SOT-?223|SOT-?89|SC-?70-?\d?|SOIC-?\d+|TSSOP-?\d+|MSOP-?\d+|"
                  r"VSSOP-?\d+|QFN-?\d+|DFN-?\d+|SON-?\d+|DO-?214A[ABC]|SOD-?\d+)", footprint or "", re.I)
    return m.group(1).upper() if m else None


def _norm_pkg(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def dk_capacitance(farads: float) -> str:
    """DigiKey's description style: 0.1UF, 47UF, 10000PF, 22PF."""
    if farads >= 0.1e-6 - 1e-15:
        return f"{farads * 1e6:g}UF"
    return f"{round(farads * 1e12):d}PF"


_STD_VOLTAGES =(6.3, 10, 16, 25, 35, 50, 63, 100, 250)


def _rated_for(volts: float) -> float:
    """The lowest standard capacitor rating at or above `volts`."""
    return next((v for v in _STD_VOLTAGES if v >= volts), volts)


# --- the lookups ---


def _stock(part: DigiKeyPart, v: Variation | None) -> int | None:
    """Stock of the packaging you'd buy — the product total counts reels you can't use."""
    return v.stock if v is not None and v.stock is not None else part.stock


def _offer(part: DigiKeyPart, qty: int) -> Offer:
    v = part.variation_for(qty)
    specs = dict(photo_url=part.photo_url, category=part.category, parameters=dict(part.parameters))
    if v is None and part.bulk_only(qty):
        # Not sold in this few: offer the smallest order it IS sold in.
        v = part.smallest_order()
        unit = v.price_at(v.min_qty)
        return Offer(
            mpn=part.mpn, manufacturer=part.manufacturer, description=part.description,
            digikey_pn=v.digikey_pn, packaging=v.package, unit_price=unit, stock=_stock(part, v),
            lifecycle=part.lifecycle, product_url=part.product_url, datasheet_url=part.datasheet_url,
            min_order=v.min_qty, min_order_cost=round(v.min_qty * unit, 2) if unit is not None else None,
            bulk_only=True, **specs,
        )
    return Offer(
        mpn=part.mpn,
        manufacturer=part.manufacturer,
        description=part.description,
        digikey_pn=part.pn_for(qty),
        packaging=v.package if v else "",
        unit_price=part.price_at(qty),
        stock=_stock(part, v),
        lifecycle=part.lifecycle,
        product_url=part.product_url,
        datasheet_url=part.datasheet_url,
        min_order=v.min_qty if v else None,
        **specs,
    )


def _active(part: DigiKeyPart) -> bool:
    return (part.lifecycle or "").lower() == "active"


def _buyable(part: DigiKeyPart, qty: int) -> bool:
    return _active(part) and (part.stock or 0) >= qty and part.price_at(qty) is not None


def _unique(parts: list[DigiKeyPart], qty: int) -> list[Offer]:
    seen, out = set(), []
    for p in parts:
        if p.mpn not in seen:
            seen.add(p.mpn)
            out.append(_offer(p, qty))
    return out


def _ranked(parts: list[DigiKeyPart], qty: int) -> list[Offer]:
    """Active parts you can buy `qty` of, cheapest first."""
    return _unique(sorted((p for p in parts if _buyable(p, qty)), key=lambda p: p.price_at(qty) or 1e9), qty)


def _bulk_ranked(parts: list[DigiKeyPart], qty: int) -> list[Offer]:
    """Active parts only sold in bigger lots, cheapest minimum order first."""
    bulk = [p for p in parts if _active(p) and p.bulk_only(qty)]

    def min_spend(p: DigiKeyPart) -> float:
        v = p.smallest_order()
        return v.min_qty * (v.price_at(v.min_qty) or 1e9)

    return _unique(sorted(bulk, key=min_spend), qty)


def bulk_note(o: Offer, qty: int) -> str:
    cost = f" (${o.min_order_cost:,.2f})" if o.min_order_cost is not None else ""
    return f"only sold in lots of {o.min_order:,}{cost} — you need {qty}"


def _pick(proposal: BomProposal, parts: list[DigiKeyPart], qty: int, why: str) -> BomProposal:
    offers = _ranked(parts, qty)
    if not offers:
        bulk = _bulk_ranked(parts, qty)
        if bulk:
            proposal.offer, proposal.alternatives = bulk[0], bulk[1 : 1 + MAX_ALTERNATIVES]
            proposal.rationale = f"{why} — every match is {bulk_note(bulk[0], qty)}"
            return proposal
        proposal.status = "skipped"
        proposal.rationale = why + " — nothing active and in stock matched."
        return proposal
    proposal.offer, proposal.alternatives = offers[0], offers[1 : 1 + MAX_ALTERNATIVES]
    proposal.rationale = why
    return proposal


def _by_part_number(client: DigiKeyClient, key: str, row: BomRow, p: BomProposal) -> BomProposal:
    exact = client.product_details(key)
    if exact is not None and exact.mpn:
        offer = _offer(exact, row.qty)
        alternatives: list[Offer] = []
        why = f"exact part number {key}"
        if not _buyable(exact, row.qty):
            if not _active(exact):
                problem = f"it's {exact.lifecycle}"
            elif offer.bulk_only:
                problem = f"it's {bulk_note(offer, row.qty)}"
            else:
                problem = f"only {exact.stock or 0} in stock"
            why += f" — but {problem}; alternatives are versions of {base_mpn(key)} you can buy {row.qty} of"
            alternatives = [o for o in _ranked(client.keyword_search(base_mpn(key), SEARCH_LIMIT), row.qty)
                            if o.mpn != exact.mpn][:MAX_ALTERNATIVES]
        p.offer, p.alternatives, p.rationale = offer, alternatives, why
        return p
    # A generic number (74AHCT1G125): several makers sell it in several packages.
    found = [x for x in client.keyword_search(key, SEARCH_LIMIT) if key.upper() in x.mpn.upper()]
    pkg = package_hint(row.footprint)
    why = f"no exact match for {key}; cheapest orderable version"
    if pkg:
        in_pkg = [x for x in found if _norm_pkg(pkg) in _norm_pkg(
            _param(x, "Package / Case", "Supplier Device Package") + " " + x.description)]
        if in_pkg:
            found, why = in_pkg, why + f" in {pkg} (from the footprint)"
        else:
            why += f" — none listed as {pkg}; check the package matches the footprint"
    return _pick(p, found, row.qty, why)


def _resistor(client: DigiKeyClient, row: BomRow, ohms: float, divider: bool, p: BomProposal) -> BomProposal:
    size = case_size(row.footprint)
    tol = 1.0 if divider else 5.0
    words = f"RES {fmt(ohms).upper()} OHM 1% {size or ''}".strip()
    found = []
    for x in client.keyword_search(words, SEARCH_LIMIT):
        r = dk_number(_param(x, "Resistance"))
        t = dk_number(_param(x, "Tolerance"))
        case = _param(x, "Package / Case")
        if r is None or abs(r - ohms) > ohms * 0.005:
            continue
        if t is not None and t > tol:
            continue
        if size and size not in case:
            continue
        found.append(x)
    why = f"{fmt(ohms)}Ω, ≤{tol:g}%" + (" (feedback divider)" if divider else "") + (f", {size}" if size else "")
    return _pick(p, found, row.qty, why)


def _capacitor(client: DigiKeyClient, row: BomRow, farads: float, volts: float | None, p: BomProposal) -> BomProposal:
    size = case_size(row.footprint)
    need = _rated_for(2 * volts) if volts else 16.0
    small = farads <= 1e-9
    words = f"CAP CER {dk_capacitance(farads)} {size or ''}".strip()
    found = []
    for x in client.keyword_search(words, SEARCH_LIMIT):
        c = dk_number(_param(x, "Capacitance"))
        v = dk_number(_param(x, "Voltage - Rated"))
        tc = _param(x, "Temperature Coefficient").upper()
        case = _param(x, "Package / Case")
        if c is None or abs(c - farads) > farads * 0.01:
            continue
        if v is None or v < need:
            continue
        if not any(d in tc for d in (("C0G", "NP0", "X7R", "X5R") if small else ("X7R", "X5R", "X7S"))):
            continue
        if size and size not in case:
            continue
        found.append(x)
    rail = f"2× its {volts:g} V rail" if volts else "no rail voltage known — assumed"
    why = f"{fmt(farads, 'F')}, ≥{need:g} V ({rail}), X7R/X5R" + (f", {size}" if size else "")
    return _pick(p, found, row.qty, why)


def _rail_volts(cmap: cm.CircuitMap, refs: list[str]) -> float | None:
    vs = [pin.voltage for r in refs if r in cmap.parts for pin in cmap.parts[r].pins if pin.voltage]
    return max(vs) if vs else None


def _skip(p: BomProposal, why: str) -> BomProposal:
    p.status, p.rationale = "skipped", why
    return p


def propose_row(client: DigiKeyClient, row: BomRow, cmap: cm.CircuitMap) -> BomProposal:
    p = BomProposal(
        id=row.id, refdes=row.refdes, qty=row.qty, value=row.value, footprint=row.footprint,
        current_mpn=row.mpn, current_price=row.unit_price,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    ref = row.refdes[0] if row.refdes else ""
    kind = cm.part_kind(ref, row.value, row.footprint) if ref else "other"
    key = row.mpn or (row.value if looks_like_mpn(row.value) else None)
    try:
        if kind == "mechanical":
            return _skip(p, "not a purchased part")
        if row.mpn or (key and kind not in ("resistor", "capacitor")):
            return _by_part_number(client, key, row, p)
        value = parse_value(row.value)
        if kind == "resistor" and value:
            divider = any(s.kind == "divider" and set(s.refs) & set(row.refdes) for s in cmap.structures)
            return _resistor(client, row, value, divider, p)
        if kind == "capacitor" and value:
            return _capacitor(client, row, value, _rail_volts(cmap, row.refdes), p)
        if kind == "inductor":
            fp = row.footprint or ""
            if fp.startswith("Resistor_SMD") or "R_0" in fp:
                return _skip(p, f"inductor on a resistor footprint ({fp.split(':')[-1]}) — check the "
                                "schematic; then pick a part by its saturation current")
            return _skip(p, "inductors are chosen by saturation current and DCR — ask the copilot, "
                            "then set the MPN")
        return _skip(p, "generic symbol — set an MPN on the part (or ask the copilot to pick one)")
    except (DigiKeyError, requests.RequestException) as e:
        return _skip(p, f"DigiKey: {e}")


# --- persistence + approval ---


class ProposalStore:
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / PROPOSALS_REL
        self._lock = threading.Lock()

    def list(self) -> list[BomProposal]:
        """Proposals for rows still on the BOM — a row dropped by a re-fill from
        the schematic (its footprint changed) takes its proposal with it."""
        data = read_json(self.path)
        if not isinstance(data, dict):
            return []
        rows = {r.id for r in BomStore.for_project(self.project_dir).list()}
        return [p for p in (BomProposal.model_validate(d) for d in data.get("proposals", [])) if p.id in rows]

    def _save(self, proposals: list[BomProposal]) -> None:
        write_json(self.path, {"proposals": [p.model_dump() for p in proposals]})

    def replace(self, fresh: list[BomProposal]) -> list[BomProposal]:
        """New proposals replace older ones for the same rows; others stay."""
        with self._lock:
            ids = {p.id for p in fresh}
            kept = [p for p in self.list() if p.id not in ids]
            out = kept + fresh
            self._save(out)
            return out

    def _set_status(self, pid: str, status: ProposalStatus) -> BomProposal:
        props = self.list()
        for p in props:
            if p.id == pid:
                p.status = status
                self._save(props)
                return p
        raise KeyError(pid)

    def approve(self, pid: str, choice: int = 0) -> BomRow:
        """Copy offer `choice` (0 = the proposal, 1.. = alternatives) onto the row."""
        with self._lock:
            p = next((x for x in self.list() if x.id == pid), None)
            if p is None:
                raise KeyError(pid)
            offers = ([p.offer] if p.offer else []) + p.alternatives
            if not 0 <= choice < len(offers):
                raise ValueError(f"no offer {choice} on {pid}")
            o = offers[choice]
            store = BomStore.for_project(self.project_dir)
            row = store.get(p.id)
            if row is None:
                raise KeyError(pid)
            row = store.update(
                p.id,
                mpn=o.mpn,
                manufacturer=o.manufacturer or None,
                distributor_pn=o.digikey_pn,
                unit_price=o.unit_price,
                stock=o.stock,
                lifecycle=o.lifecycle,
                product_url=o.product_url or row.product_url,
                datasheet_url=row.datasheet_url or o.datasheet_url,
                fetched_at=p.created_at,
                # Only a lot bigger than the row matters; min 1 would just be noise.
                min_order=o.min_order if (o.min_order or 0) > row.qty else None,
            )
            self._set_status(pid, "approved")
            return row

    def reject(self, pid: str) -> BomProposal:
        with self._lock:
            return self._set_status(pid, "rejected")

    def approve_all(self) -> list[BomRow]:
        pending = [p.id for p in self.list() if p.status == "pending" and p.offer]
        return [self.approve(pid) for pid in pending]


def propose(
    project_dir: str | Path,
    design: Design,
    client: DigiKeyClient,
    row_ids: list[str] | None = None,
) -> list[BomProposal]:
    """Look up the BOM's rows (or just `row_ids`) and store proposals. Rows the
    engineer pinned or rejected are left alone, and a whole-BOM fill skips rows
    that already have a part link (only the engineer, by hand or by approving a
    proposal, sets one); name them in `row_ids` to re-look."""
    rows = [
        r for r in BomStore.for_project(project_dir).list()
        if r.status == "active"
        and (r.id in row_ids if row_ids is not None else not r.product_url)
    ]
    cmap = cm.build_circuit_map(design)
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        fresh = list(pool.map(lambda r: propose_row(client, r, cmap), rows))
    ProposalStore(project_dir).replace(fresh)
    return fresh


def lifecycle_findings(project_dir: str | Path) -> list:
    """A `verify` finding for each BOM part DigiKey lists as not Active
    (Not For New Designs, Last Time Buy, Obsolete, Discontinued)."""
    from pcb_assistant.findings.models import Finding

    out = []
    for r in BomStore.for_project(project_dir).list():
        life = (r.lifecycle or "").strip()
        if r.status == "rejected" or not life or life.lower() == "active":
            continue
        out.append(Finding(
            severity="verify",
            title=f"{r.mpn} is '{life}' at DigiKey",
            detail=(f"{', '.join(r.refdes)} use {r.mpn} ({r.manufacturer or 'unknown maker'}), which "
                    f"DigiKey lists as {life}. Pick an active equivalent before ordering, or "
                    "confirm you have stock."),
            failure_mode="the part may be unavailable when the board is built",
            citation=f"DigiKey product status ({(r.fetched_at or '')[:10]})",
            source="distributor",
            component_ref=r.refdes[0] if r.refdes else None,
            rule_id=f"distributor:lifecycle:{r.mpn}",
        ))
    return out


def summary(proposals: list[BomProposal]) -> str:
    """One line per proposal, for the chat agent."""
    lines = []
    for p in proposals:
        who = ",".join(p.refdes) or p.id
        if p.offer:
            price = f"${p.offer.unit_price:.3f}" if p.offer.unit_price is not None else "no price"
            bulk = f" [BULK: {bulk_note(p.offer, p.qty)}]" if p.offer.bulk_only else ""
            lines.append(f"{who}: {p.offer.mpn} ({p.offer.manufacturer}) {price}{bulk} — {p.rationale}")
        else:
            lines.append(f"{who}: skipped — {p.rationale}")
    return "\n".join(lines)
