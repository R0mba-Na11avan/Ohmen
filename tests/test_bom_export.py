"""The BOM exports: CSV layout stays put; the Excel sheet groups and flags parts."""
import io
import zipfile
from datetime import date

from openpyxl import load_workbook

from pcb_assistant.bom.export import (
    BOARD_ROW,
    BUILD_ROW,
    HEADER_ROW,
    ORDER_ROW,
    UNPRICED_ROW,
    XLSX_COLUMNS,
    bom_xlsx,
    content_disposition,
    export_filename,
    sections,
)
from pcb_assistant.bom.models import BomRow

COL = {h: i + 1 for i, (h, _) in enumerate(XLSX_COLUMNS)}


def row(id, refdes=(), **kw):
    return BomRow(id=id, refdes=list(refdes), qty=max(len(refdes), 1), **kw)


ROWS = [
    row("c", ["C1", "C2"], value="100n", mpn="CAP", unit_price=0.1, stock=5000, lifecycle="Active"),
    row("cr", ["CR1"], value="SMBJ30A", mpn="TVS", unit_price=0.38),
    row("d", ["D1"], value="LED", mpn="LED1", unit_price=0.16, lifecycle="Obsolete"),
    row("j", ["J1"], value="XT30", unit_price=9.99, min_order=5),
    row("r", ["R1"], value="10k", mpn="RES", stock=0),
    row("tp", ["TP1"], value="TP", mpn="TP", unit_price=0.0),
    row("rej", ["R2"], value="1k", mpn="=HYPERLINK(\"x\")", unit_price=1.0, status="rejected"),
    row("plan", [], mpn="ESP32", unit_price=5.21, source="llm", datasheet_url="https://example.com/ds.pdf"),
]


def sheet(rows=ROWS):
    return load_workbook(io.BytesIO(bom_xlsx(rows, "Board", today=date(2026, 11, 19)))).active


def first_col(ws):
    return [ws.cell(row=r, column=1).value for r in range(1, ws.max_row + 1)]


def test_sections_merge_synonyms_and_put_unplaced_last():
    names = [t.name for t, _ in sections(ROWS)]
    assert names == ["Capacitors", "Diodes", "Connectors", "Resistors", "Other (TP)", "Unplaced"]
    diodes = dict((t.name, g) for t, g in sections(ROWS))["Diodes"]
    assert [r.id for r in diodes] == ["cr", "d"]


def test_title_summary_header_freeze_and_print():
    ws = sheet()
    assert ws["A1"].value == "Board — Bill of Materials"
    # Rejected R2 is left out of the counts.
    assert ws["A2"].value == "Exported 2026-11-19 · 7 lines · 8 parts per board · prices in USD"
    assert [ws.cell(row=HEADER_ROW, column=c).value for c in range(1, len(XLSX_COLUMNS) + 1)] == [h for h, _ in XLSX_COLUMNS]
    assert ws.freeze_panes == f"B{HEADER_ROW + 1}"
    assert ws.auto_filter.ref is None
    assert ws.page_setup.orientation == "landscape" and ws.print_title_rows == f"${HEADER_ROW}:${HEADER_ROW}"


def test_summary_is_live_and_build_qty_is_the_input():
    ws = sheet()
    last = ws.max_row
    assert ws[f"B{BUILD_ROW}"].value == 1
    assert ws[f"B{BOARD_ROW}"].value == f"=$M${last}"
    assert ws[f"B{ORDER_ROW}"].value == f"=$P${last}"
    unpriced = ws[f"B{UNPRICED_ROW}"].value
    assert unpriced.startswith("=COUNTIFS(") and '"<>rejected"' in unpriced


def test_section_rows_subtotals_and_totals_are_formulas():
    ws = sheet()
    col = first_col(ws)
    assert col[HEADER_ROW] == "Capacitors (1 line)"
    assert "Diodes (2 lines)" in col and "Unplaced (1 line)" in col and col[-1] == "TOTAL"
    assert "Resistors (1 line)" in col  # rejected R2 not counted
    heads = [i + 1 for i, v in enumerate(col) if isinstance(v, str) and v.endswith(("line)", "lines)"))]
    for h in heads:
        for name in ("Board cost", "Order cost"):
            cell = ws.cell(row=h, column=COL[name])
            assert cell.value.startswith("=SUM(") and cell.data_type == "f"  # live, not text
    for name, letter in (("Board cost", "M"), ("Order cost", "P")):
        total = ws.cell(row=ws.max_row, column=COL[name])
        assert total.value == "=SUM(" + ",".join(f"{letter}{h}" for h in heads) + ")" and total.data_type == "f"


def test_board_cost_ignores_lots_and_order_cost_buys_them():
    ws = sheet()
    col = first_col(ws)
    j = col.index("J1") + 1
    f = lambda name: ws.cell(row=j, column=COL[name]).value
    assert f("Board cost") == f'=IF(L{j}="","",B{j}*L{j})'
    assert f("Order qty") == f"=MAX(B{j}*$B${BUILD_ROW},K{j})"
    assert f("Order cost") == f'=IF(L{j}="","",O{j}*L{j})'
    assert f("% of board").startswith(f'=IF(OR(M{j}="",')
    rej = col.index("R2") + 1
    assert ws.cell(row=rej, column=COL["Board cost"]).value == 0
    assert ws.cell(row=rej, column=COL["Order qty"]).value is None
    assert ws.cell(row=rej, column=1).font.strike


def test_placeholders_and_routine_values_are_blank():
    ws = sheet([row("a", ["C1"], why="from schematic", fetched_at="2026-11-12T10:00:00")])
    r = HEADER_ROW + 2
    assert ws.cell(row=r, column=COL["Why"]).value is None
    assert ws.cell(row=r, column=COL["Status"]).value is None  # active
    assert ws.cell(row=r, column=COL["Checked"]).value == "2026-11-12"


def test_problem_cells_are_flagged():
    ws = sheet()
    col = first_col(ws)
    fill = lambda r, h: ws.cell(row=col.index(r) + 1, column=COL[h]).fill.fgColor.rgb[-6:]
    assert fill("D1", "Lifecycle") == "FFC7CE"  # obsolete
    assert fill("C1 C2", "Lifecycle") != "FFC7CE"  # active
    assert fill("R1", "Unit price") == "FFEB9C"  # no price
    assert fill("J1", "MPN") == "FFEB9C"  # no MPN
    assert fill("C1 C2", "MPN") != "FFEB9C"
    # Stock is checked against the order qty, which moves with Build qty: a live rule.
    cf = ws.conditional_formatting._cf_rules.items()
    rules = {str(rng.sqref): [r.formula[0] for r in rs] for rng, rs in cf}
    # Excel paints a conditional solid fill from bgColor; fgColor shows nothing.
    assert all(r.dxf.fill.bgColor.rgb[-6:] == "FFEB9C" for _, rs in cf for r in rs)
    stock = next(v for k, v in rules.items() if k.startswith("H"))
    assert stock == [f"AND(ISNUMBER(H{HEADER_ROW + 1}),ISNUMBER(O{HEADER_ROW + 1}),H{HEADER_ROW + 1}<O{HEADER_ROW + 1})"]


def test_user_text_never_becomes_a_formula_and_links_are_clickable():
    ws = sheet()
    col = first_col(ws)
    mpn = ws.cell(row=col.index("R2") + 1, column=COL["MPN"])
    assert mpn.value == '=HYPERLINK("x")' and mpn.data_type == "s"
    ds = ws.cell(row=col.index("Unplaced (1 line)") + 2, column=COL["Datasheet"])
    assert ds.value == "https://example.com/ds.pdf" and ds.hyperlink.target == ds.value


def test_widths_fit_content_with_caps():
    ws = sheet([row("a", ["U1"], value="x" * 200, footprint="Package_SO:SOIC-8")])
    width = lambda h: ws.column_dimensions[ws.cell(row=HEADER_ROW, column=COL[h]).column_letter].width
    assert width("Value") == 24  # capped
    assert width("Footprint") == round(len("Package_SO:SOIC-8") * 1.15) + 2


def test_empty_bom_still_exports():
    ws = sheet([])
    assert first_col(ws)[-1] == "TOTAL"


def test_filenames():
    assert export_filename("my board", "xlsx", date(2026, 11, 19)) == "my board-BOM-2026-11-19.xlsx"
    assert export_filename('a/b:c"', "csv", date(2026, 1, 2)) == "a_b_c_-BOM-2026-01-02.csv"
    h = content_disposition("Ωhm-BOM-2026-11-19.csv")
    assert 'filename="_hm-BOM-2026-11-19.csv"' in h and "filename*=UTF-8''%CE%A9hm" in h


def test_excel_error_triangles_are_switched_off():
    data = bom_xlsx(ROWS, "Board")
    xml = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert '<pageMargins' in xml and xml.index("<pageMargins") < xml.index("<ignoredErrors>")
    assert 'formula="1"' in xml and 'numberStoredAsText="1"' in xml
    load_workbook(io.BytesIO(data))  # still a valid workbook
