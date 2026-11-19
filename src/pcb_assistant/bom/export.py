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
    ("MPN", 28), ("Manufacturer", 28), ("DigiKey PN", 28), ("Stock", 10), ("Lifecycle", 22),
    ("Checked", 12), ("Min order", 10),
    ("Unit price", 12), ("Board cost", 12), ("% of board", 11), ("Order qty", 10), ("Order cost", 12),
    ("Why", 60), ("Datasheet", 40), ("Product", 40), ("Source", 11), ("Status", 9),
]
_GROUP_STARTS = {"MPN", "Unit price", "Why"}  # a divider runs down the left of these
_NUMERIC = {"Qty", "Stock", "Min order", "Unit price", "Board cost", "% of board", "Order qty", "Order cost"}
_COL = {h: i + 1 for i, (h, _) in enumerate(XLSX_COLUMNS)}
_PRICE_FMT = '"$"#,##0.00##'
_MONEY_FMT = '"$"#,##0.00'
_PCT_FMT = "0.0%"
_BAD_LIFECYCLE = ("obsolete", "not for new designs", "nrnd", "discontinued", "last time")
_PLACEHOLDER_WHY = "from schematic"  # what a schematic fill writes when there's no reason yet

# Title block, a live summary (Build qty is its one input), then the table.
TITLE_ROW, INFO_ROW = 1, 2
BUILD_ROW, BOARD_ROW, ORDER_ROW, UNPRICED_ROW = 3, 4, 5, 6
HEADER_ROW = 8


def _letter(header: str) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(_COL[header])


def bom_xlsx(rows: list[BomRow], project_name: str, today: date | None = None) -> bytes:
    """The BOM as a formatted .xlsx workbook.

    Two costs, kept apart: Board cost is one board's parts (qty × price); Order
    cost is buying for `Build qty` boards, where a part sold only in lots of N
    costs the whole lot. Costs, % of board and the summary are live formulas, so
    editing a price or the build qty in Excel updates them.
    """
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ncols = len(XLSX_COLUMNS)
    widths = {i: len(h) + 2 for i, (h, _) in enumerate(XLSX_COLUMNS, 1)}
    col = {h: _letter(h) for h, _ in XLSX_COLUMNS}

    thin = Side(style="thin", color="D9D9D9")
    divider = Side(style="medium", color="8C8C8C")
    bad = (PatternFill("solid", fgColor="FFC7CE"), Font(color="9C0006"))
    warn = (PatternFill("solid", fgColor="FFEB9C"), Font(color="9C5700"))
    muted = Font(color="595959")
    # A conditional format's solid fill reads bgColor, not fgColor (Excel quirk).
    warn_rule = dict(fill=PatternFill("solid", bgColor="FFEB9C"), font=Font(color="9C5700"))

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

    def total_cell(r: int, h: str, formula: str, fmt: str = _MONEY_FMT):
        cell = put(r, _COL[h], formula, width="$00,000.00", formula=True)
        cell.number_format, cell.font = fmt, Font(bold=True)

    def borders(r: int, stripe: str | None = None, heavy_bottom: bool = False):
        for c in range(1, ncols + 1):
            left = divider if XLSX_COLUMNS[c - 1][0] in _GROUP_STARTS else None
            if c == 1 and stripe:
                left = Side(style="thick", color=stripe)
            right = divider if c == ncols else None
            ws.cell(row=r, column=c).border = Border(
                left=left, right=right, bottom=divider if heavy_bottom else thin,
            )

    # The table is section heading, its parts, ..., TOTAL. Work out where TOTAL
    # lands first so the summary above can point at it.
    groups = sections(rows)
    first_data = HEADER_ROW + 1
    total_row = first_data + sum(1 + len(g) for _, g in groups)
    build = f"$B${BUILD_ROW}"
    board_total = f"${col['Board cost']}${total_row}"

    # Title block.
    counted = [r for r in rows if r.status != "rejected"]
    ws.cell(row=TITLE_ROW, column=1, value=f"{project_name} — Bill of Materials").font = Font(bold=True, size=14)
    ws.cell(row=INFO_ROW, column=1, value=(
        f"Exported {(today or date.today()).isoformat()} · {len(counted)} lines · "
        f"{sum(r.qty for r in counted)} parts per board · prices in USD"
    )).font = muted

    # Summary: labels in A, values in B:C, a plain-words note in D.
    for r, text, note in (
        (BUILD_ROW, "Build qty", "change this to cost a production run"),
        (BOARD_ROW, "Board cost", "one board's parts: qty × unit price"),
        (ORDER_ROW, "Order cost", "buying for the build; parts sold in lots cost the whole lot"),
        (UNPRICED_ROW, "Unpriced lines", "not in either cost until they have a price"),
    ):
        ws.cell(row=r, column=1, value=text).font = muted
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        ws.cell(row=r, column=2).alignment = Alignment(horizontal="right")
        ws.cell(row=r, column=4, value=note).font = muted
    b = ws.cell(row=BUILD_ROW, column=2, value=1)  # the input: blue on yellow, by convention
    b.font = Font(bold=True, color="0000FF")
    b.fill = PatternFill("solid", fgColor="FFF2CC")
    b.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    b.number_format = '[=1]0" board";0" boards"'
    for r, ref in ((BOARD_ROW, board_total), (ORDER_ROW, f"${col['Order cost']}${total_row}")):
        c = ws.cell(row=r, column=2, value=f"={ref}")
        c.number_format, c.font = _MONEY_FMT, Font(bold=True)
    span = lambda h: f"${col[h]}${first_data}:${col[h]}${total_row - 1}"
    # A line that wants a price: has a qty (section rows don't), isn't rejected, has none.
    ws.cell(row=UNPRICED_ROW, column=2, value=(
        f'=COUNTIFS({span("Unit price")},"",{span("Qty")},"<>",{span("Status")},"<>rejected")'
    )).font = Font(bold=True)
    ws.conditional_formatting.add(
        f"B{UNPRICED_ROW}", FormulaRule(formula=[f"$B${UNPRICED_ROW}>0"], **warn_rule),
    )

    # Header.
    head_fill = PatternFill("solid", fgColor="1F2937")
    for c, (h, _) in enumerate(XLSX_COLUMNS, 1):
        cell = ws.cell(row=HEADER_ROW, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="right" if h in _NUMERIC else "left")
    borders(HEADER_ROW, heavy_bottom=True)

    heads: list[int] = []
    r = first_data
    for ptype, group in groups:
        head = r
        heads.append(head)
        live = sum(1 for g in group if g.status != "rejected")
        fill = PatternFill("solid", fgColor=ptype.fill)
        put(head, 1, f"{ptype.name} ({live} line{'' if live == 1 else 's'})").font = Font(bold=True)
        for c in range(1, ncols + 1):
            ws.cell(row=head, column=c).fill = fill
        borders(head, stripe=ptype.stripe)
        r += 1
        for row in group:
            _write_row(put, ws, r, row, col, build, board_total, bad, warn)
            borders(r, stripe=ptype.stripe)
            r += 1
        for h in ("Board cost", "Order cost"):
            total_cell(head, h, f"=SUM({col[h]}{head + 1}:{col[h]}{r - 1})")
        total_cell(head, "% of board", f'=IF({board_total}=0,"",{col["Board cost"]}{head}/{board_total})', _PCT_FMT)

    # TOTAL: the section subtotals added up.
    put(r, 1, "TOTAL").font = Font(bold=True)
    for h in ("Board cost", "Order cost"):
        total_cell(r, h, f"=SUM({','.join(f'{col[h]}{x}' for x in heads)})" if heads else "=0")
    for c in range(1, ncols + 1):
        ws.cell(row=r, column=c).border = Border(
            left=divider if c == 1 or XLSX_COLUMNS[c - 1][0] in _GROUP_STARTS else None,
            right=divider if c == ncols else None, top=divider, bottom=divider,
        )

    # Stock short of what the build orders. Order qty moves with Build qty, so
    # this is a live rule, not a fill fixed at export time.
    if rows:
        s, oq = col["Stock"], col["Order qty"]
        ws.conditional_formatting.add(
            f"{s}{first_data}:{s}{total_row - 1}",
            FormulaRule(
                formula=[f"AND(ISNUMBER({s}{first_data}),ISNUMBER({oq}{first_data}),{s}{first_data}<{oq}{first_data})"],
                **warn_rule,
            ),
        )

    for c, (_, cap) in enumerate(XLSX_COLUMNS, 1):
        ws.column_dimensions[col[XLSX_COLUMNS[c - 1][0]]].width = min(widths[c], cap)
    ws.freeze_panes = ws.cell(row=HEADER_ROW + 1, column=2)  # header row + Refdes column

    # Print: landscape, one page wide, the header row on every page.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"

    buf = io.BytesIO()
    wb.save(buf)
    return _ignore_errors(buf.getvalue(), f"A1:{col['Status']}{total_row}")


def _ignore_errors(xlsx: bytes, ref: str) -> bytes:
    """Turn off Excel's green-triangle checks over the sheet. Line formulas sit
    between subtotal rows ('inconsistent formula') and values like 330 are text
    on purpose. openpyxl can't write <ignoredErrors>, so add it to the sheet XML.
    Schema order puts it after pageMargins, pageSetup and headerFooter."""
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
                at = xml.rindex("</worksheet>")
                for pat in (r"<headerFooter\b.*?(?:/>|</headerFooter>)", r"<pageSetup\b[^>]*/>", r"<pageMargins\b[^>]*/>"):
                    m = re.search(pat, xml, re.S)
                    if m:
                        at = m.end()
                        break
                data = (xml[:at] + tag + xml[at:]).encode("utf-8")
            dst.writestr(item, data)
    return out.getvalue()


def _write_row(put, ws, r: int, row: BomRow, col: dict[str, str], build: str, board_total: str, bad, warn) -> None:
    from openpyxl.styles import Font

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
        "Checked": (row.fetched_at or "")[:10] or None,  # blank = never checked on DigiKey
        "Min order": row.min_order,
        "Unit price": row.unit_price,
        "Why": None if row.why in ("", _PLACEHOLDER_WHY) else row.why,
        "Datasheet": row.datasheet_url,
        "Product": row.product_url,
        "Source": row.source,
        "Status": None if row.status == "active" else row.status,  # only the exceptions
    }
    for h, v in values.items():
        cell = put(r, _COL[h], v)
        if h in ("Datasheet", "Product") and isinstance(v, str) and v.startswith(("http://", "https://")):
            cell.hyperlink = v
            cell.style = "Hyperlink"
        if h == "Unit price":
            cell.number_format = _PRICE_FMT

    q, p, mo = f"{col['Qty']}{r}", f"{col['Unit price']}{r}", f"{col['Min order']}{r}"
    oq, bc = f"{col['Order qty']}{r}", f"{col['Board cost']}{r}"

    def live(h: str, formula, fmt: str, shown: str) -> None:
        put(r, _COL[h], formula, width=shown, formula=True).number_format = fmt

    if rejected:
        live("Board cost", 0, _PRICE_FMT, "$0.00")
        live("Order cost", 0, _PRICE_FMT, "$0.00")
        for c in range(1, len(XLSX_COLUMNS) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(color="A6A6A6", strike=True, underline=cell.font.underline)
        return

    live("Board cost", f'=IF({p}="","",{q}*{p})', _PRICE_FMT, "$0000.00")
    live("% of board", f'=IF(OR({bc}="",{board_total}=0),"",{bc}/{board_total})', _PCT_FMT, "100.0%")
    live("Order qty", f"=MAX({q}*{build},{mo})", "0", "00000")  # lots of N are bought N at a time
    live("Order cost", f'=IF({p}="","",{oq}*{p})', _PRICE_FMT, "$0000.00")

    def flag(h: str, style) -> None:
        cell = ws.cell(row=r, column=_COL[h])
        cell.fill, cell.font = style

    if row.lifecycle and any(s in row.lifecycle.lower() for s in _BAD_LIFECYCLE):
        flag("Lifecycle", bad)
    if row.unit_price is None:
        flag("Unit price", warn)
    if not row.mpn:
        flag("MPN", warn)
