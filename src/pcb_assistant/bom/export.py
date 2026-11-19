"""BOM exports: a plain CSV for distributor importers, and a formatted Excel sheet
for people to read (part-type sections, live cost formulas, problem cells)."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from pcb_assistant.bom.models import BomRow

# --- file names ---


def export_filename(project_name: str, ext: str, today: date | None = None) -> str:
    """'<project>-BOM-<YYYY-MM-DD>.<ext>', so repeated exports don't overwrite."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", project_name).strip(" .") or "project"
    return f"{name}-BOM-{(today or date.today()).isoformat()}.{ext}"


def content_disposition(filename: str) -> str:
    """Attachment header with an ASCII fallback plus the exact UTF-8 name."""
    ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# --- CSV (unchanged layout: importers depend on it) ---

CSV_COLUMNS = [
    "refdes", "qty", "value", "footprint", "mpn", "manufacturer", "distributor_pn",
    "unit_price", "line_total", "why", "datasheet_url", "product_url", "source", "status",
]


def bom_csv(rows: list[BomRow]) -> str:
    """The full BOM as CSV, with line totals and a board total. Starts with a
    byte-order mark so Excel reads UTF-8 and 10kΩ and 4.7µF survive."""

    def cell(v):
        # A spreadsheet runs text starting with '=' or '@' as a formula.
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "@") else v

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    total = 0.0
    for r in rows:
        lt = r.line_total
        if lt:
            total += lt
        w.writerow([cell(v) for v in [
            " ".join(r.refdes), r.qty, r.value or "", r.footprint or "", r.mpn or "",
            r.manufacturer or "", r.distributor_pn or "",
            "" if r.unit_price is None else r.unit_price,
            "" if lt is None else lt,
            r.why, r.datasheet_url or "", r.product_url or "", r.source, r.status,
        ]])
    li = CSV_COLUMNS.index("line_total")
    w.writerow(["TOTAL"] + [""] * (li - 1) + [round(total, 2)] + [""] * (len(CSV_COLUMNS) - li - 1))
    return "﻿" + buf.getvalue()


# --- part-type sections ---


@dataclass(frozen=True)
class PartType:
    name: str
    fill: str  # light tint for the section heading row
    stripe: str  # strong color for the Refdes stripe


_TYPES = {
    "Capacitors": PartType("Capacitors", "DCEBFA", "2F75B5"),
    "Diodes": PartType("Diodes", "F8DEDE", "C0504D"),
    "Connectors": PartType("Connectors", "E8DFF5", "7E57C2"),
    "Inductors": PartType("Inductors", "D9F2EE", "26A69A"),
    "Ferrite beads": PartType("Ferrite beads", "EAF0D5", "8A9A3A"),
    "Transistors": PartType("Transistors", "FCE4CC", "E07B24"),
    "Resistors": PartType("Resistors", "DFF0D8", "4E9A3A"),
    "Switches": PartType("Switches", "FFF4CC", "C9A100"),
    "ICs": PartType("ICs", "DDE3EA", "44546A"),
    "Crystals": PartType("Crystals", "F9DCEB", "C2185B"),
    "Fuses": PartType("Fuses", "EFE3D6", "8D6E63"),
}
_PREFIX_TYPE = {
    "C": "Capacitors",
    "D": "Diodes", "CR": "Diodes", "LED": "Diodes",
    "J": "Connectors", "P": "Connectors", "CN": "Connectors",
    "L": "Inductors",
    "FB": "Ferrite beads",
    "Q": "Transistors",
    "R": "Resistors",
    "SW": "Switches", "S": "Switches",
    "U": "ICs", "IC": "ICs",
    "Y": "Crystals", "X": "Crystals",
    "F": "Fuses",
}
_OTHER = ("EDEDED", "808080")
UNPLACED = PartType("Unplaced", "F2F2F2", "A6A6A6")


def part_type(row: BomRow) -> PartType:
    """The section a row belongs in, from its first refdes prefix (CR1 -> Diodes)."""
    if not row.refdes:
        return UNPLACED
    m = re.match(r"[A-Za-z]+", row.refdes[0])
    prefix = m.group(0).upper() if m else ""
    name = _PREFIX_TYPE.get(prefix)
    if name:
        return _TYPES[name]
    return PartType(f"Other ({prefix})" if prefix else "Other", *_OTHER)


def sections(rows: list[BomRow]) -> list[tuple[PartType, list[BomRow]]]:
    """Rows grouped by part type, in order of first appearance; Unplaced last."""
    groups: dict[str, tuple[PartType, list[BomRow]]] = {}
    for r in rows:
        t = part_type(r)
        groups.setdefault(t.name, (t, []))[1].append(r)
    unplaced = groups.pop(UNPLACED.name, None)
    return list(groups.values()) + ([unplaced] if unplaced else [])


# --- Excel ---

# (header, width cap). Grouped Part | Sourcing | Cost | Notes.
XLSX_COLUMNS = [
    ("Refdes", 30), ("Qty", 8), ("Value", 24), ("Footprint", 40),
    ("MPN", 28), ("Manufacturer", 28), ("DigiKey PN", 28), ("Stock", 10), ("Lifecycle", 22), ("Min order", 10),
    ("Unit price", 12), ("Line total", 12),
    ("Why", 60), ("Datasheet", 40), ("Product", 40), ("Source", 11), ("Status", 9),
]
_GROUP_STARTS = {"MPN", "Unit price", "Why"}  # a divider runs down the left of these
_COL = {h: i + 1 for i, (h, _) in enumerate(XLSX_COLUMNS)}
_PRICE_FMT = '"$"#,##0.00##'
_BAD_LIFECYCLE = ("obsolete", "not for new designs", "nrnd", "discontinued", "last time")

TITLE_ROW, INFO_ROW, HEADER_ROW = 1, 2, 4


def _letter(header: str) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(_COL[header])


def bom_xlsx(rows: list[BomRow], project_name: str, today: date | None = None) -> bytes:
    """The BOM as a formatted .xlsx workbook."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ncols = len(XLSX_COLUMNS)
    widths = {i: len(h) + 2 for i, (h, _) in enumerate(XLSX_COLUMNS, 1)}

    thin = Side(style="thin", color="D9D9D9")
    divider = Side(style="medium", color="8C8C8C")
    bad = (PatternFill("solid", fgColor="FFC7CE"), Font(color="9C0006"))
    warn = (PatternFill("solid", fgColor="FFEB9C"), Font(color="9C5700"))
    grey = "A6A6A6"

    def put(r: int, c: int, value, width: str | None = None, formula: bool = False):
        """Write a cell; user text never runs as a formula and loses control chars
        (only `formula=True` writes one). `width` is the text the column should
        fit (defaults to the value)."""
        cell = ws.cell(row=r, column=c)
        if isinstance(value, str):
            value = ILLEGAL_CHARACTERS_RE.sub("", value)
            cell.value = value
            if value.startswith("=") and not formula:
                cell.data_type = "s"
        else:
            cell.value = value
        shown = width if width is not None else ("" if value is None else str(value))
        widths[c] = max(widths[c], round(len(shown) * 1.15) + 2)  # Calibri caps run wide
        return cell

    def borders(r: int, stripe: str | None = None, heavy_bottom: bool = False):
        for c in range(1, ncols + 1):
            left = divider if XLSX_COLUMNS[c - 1][0] in _GROUP_STARTS else None
            if c == 1 and stripe:
                left = Side(style="thick", color=stripe)
            right = divider if c == ncols else None
            ws.cell(row=r, column=c).border = Border(
                left=left, right=right, bottom=divider if heavy_bottom else thin,
            )

    # Title block.
    counted = [r for r in rows if r.status != "rejected"]
    total = round(sum(r.line_total or 0 for r in rows), 2)
    ws.cell(row=TITLE_ROW, column=1, value=f"{project_name} — Bill of Materials").font = Font(bold=True, size=14)
    ws.cell(row=INFO_ROW, column=1, value=(
        f"Exported {(today or date.today()).isoformat()} · {len(rows)} lines · "
        f"{sum(r.qty for r in counted)} parts · ${total:,.2f}"
    )).font = Font(color="595959")

    # Header.
    head_fill = PatternFill("solid", fgColor="1F2937")
    for c, (h, _) in enumerate(XLSX_COLUMNS, 1):
        cell = ws.cell(row=HEADER_ROW, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="right" if h in ("Qty", "Stock", "Min order", "Unit price", "Line total") else "left")
    borders(HEADER_ROW, heavy_bottom=True)

    L, K, B, J = _letter("Line total"), _letter("Unit price"), _letter("Qty"), _letter("Min order")
    subtotal_cells: list[str] = []
    r = HEADER_ROW + 1
    for ptype, group in sections(rows):
        head = r
        fill = PatternFill("solid", fgColor=ptype.fill)
        put(head, 1, f"{ptype.name} ({len(group)})").font = Font(bold=True)
        for c in range(1, ncols + 1):
            ws.cell(row=head, column=c).fill = fill
        borders(head, stripe=ptype.stripe)
        r += 1
        first = r
        for row in group:
            _write_row(ws, put, r, row, (L, K, B, J), bad, warn, grey)
            borders(r, stripe=ptype.stripe)
            r += 1
        sub = put(head, _COL["Line total"], f"=SUM({L}{first}:{L}{r - 1})", width="$0000.00", formula=True)
        sub.font = Font(bold=True)
        sub.number_format = _PRICE_FMT
        subtotal_cells.append(f"{L}{head}")

    # Board total.
    put(r, 1, "TOTAL").font = Font(bold=True)
    tot = put(r, _COL["Line total"], f"=SUM({','.join(subtotal_cells)})" if subtotal_cells else 0, width="$0000.00", formula=True)
    tot.font = Font(bold=True)
    tot.number_format = _PRICE_FMT
    for c in range(1, ncols + 1):
        ws.cell(row=r, column=c).border = Border(
            left=divider if c == 1 or XLSX_COLUMNS[c - 1][0] in _GROUP_STARTS else None,
            right=divider if c == ncols else None, top=divider, bottom=divider,
        )

    for c, (_, cap) in enumerate(XLSX_COLUMNS, 1):
        ws.column_dimensions[ws.cell(row=HEADER_ROW, column=c).column_letter].width = min(widths[c], cap)
    ws.freeze_panes = ws.cell(row=HEADER_ROW + 1, column=2)  # header row + Refdes column

    buf = io.BytesIO()
    wb.save(buf)
    return _ignore_errors(buf.getvalue(), f"A1:{_letter('Status')}{r}")


def _ignore_errors(xlsx: bytes, ref: str) -> bytes:
    """Turn off Excel's green-triangle checks over the sheet. Line totals sit
    between subtotal rows ('inconsistent formula') and values like 330 are text
    on purpose. openpyxl can't write <ignoredErrors>, so add it to the sheet XML;
    it belongs right after <pageMargins>, the last element openpyxl writes here."""
    import zipfile

    tag = (f'<ignoredErrors><ignoredError sqref="{ref}" formula="1" formulaRange="1" '
           'numberStoredAsText="1" emptyCellReference="1"/></ignoredErrors>')
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode("utf-8")
                m = re.search(r"<pageMargins[^>]*/>", xml)
                at = m.end() if m else xml.rindex("</worksheet>")
                data = (xml[:at] + tag + xml[at:]).encode("utf-8")
            dst.writestr(item, data)
    return out.getvalue()


def _write_row(ws, put, r: int, row: BomRow, letters, bad, warn, grey: str) -> None:
    from openpyxl.styles import Font

    L, K, B, J = letters
    rejected = row.status == "rejected"
    values = {
        "Refdes": " ".join(row.refdes),
        "Qty": row.qty,
        "Value": row.value,
        "Footprint": row.footprint,
        "MPN": row.mpn,
        "Manufacturer": row.manufacturer,
        "DigiKey PN": row.distributor_pn,
        "Stock": row.stock,
        "Lifecycle": row.lifecycle,
        "Min order": row.min_order,
        "Unit price": row.unit_price,
        "Why": row.why or None,
        "Datasheet": row.datasheet_url,
        "Product": row.product_url,
        "Source": row.source,
        "Status": row.status,
    }
    for h, v in values.items():
        cell = put(r, _COL[h], v)
        if h in ("Datasheet", "Product") and isinstance(v, str) and v.startswith(("http://", "https://")):
            cell.hyperlink = v
            cell.style = "Hyperlink"
        if h == "Unit price":
            cell.number_format = _PRICE_FMT

    # Line total: the app's rule (a minimum lot costs the whole lot); rejected = 0.
    lt_shown = "" if row.line_total is None else f"${row.line_total:,.2f}"
    lt_value = 0 if rejected else f'=IF({K}{r}="","",MAX({B}{r},{J}{r})*{K}{r})'
    lt = put(r, _COL["Line total"], lt_value, width=lt_shown, formula=True)
    lt.number_format = _PRICE_FMT

    if rejected:
        for c in range(1, len(XLSX_COLUMNS) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(color=grey, strike=True, underline=cell.font.underline)
        return

    def flag(h: str, style) -> None:
        cell = ws.cell(row=r, column=_COL[h])
        cell.fill, cell.font = style

    if row.lifecycle and any(s in row.lifecycle.lower() for s in _BAD_LIFECYCLE):
        flag("Lifecycle", bad)
    if row.stock is not None and row.stock < row.qty:
        flag("Stock", warn)
    if row.unit_price is None:
        flag("Unit price", warn)
    if not row.mpn:
        flag("MPN", warn)
