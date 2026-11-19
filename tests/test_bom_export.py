"""The BOM exports: CSV layout stays put; the Excel sheet groups and flags parts."""
import io
from datetime import date

from openpyxl import load_workbook

from pcb_assistant.bom.export import (
    HEADER_ROW,
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


def test_title_header_and_freeze():
    ws = sheet()
    assert ws["A1"].value == "Board — Bill of Materials"
    assert ws["A2"].value.startswith("Exported 2026-11-19 · 8 lines · ")
    assert [ws.cell(row=HEADER_ROW, column=c).value for c in range(1, len(XLSX_COLUMNS) + 1)] == [h for h, _ in XLSX_COLUMNS]
    assert ws.freeze_panes == f"B{HEADER_ROW + 1}"
    assert ws.auto_filter.ref is None


def test_section_rows_subtotals_and_total_are_formulas():
    ws = sheet()
    col = first_col(ws)
    assert col[HEADER_ROW] == "Capacitors (1)"
    assert "Diodes (2)" in col and "Unplaced (1)" in col and col[-1] == "TOTAL"
    lt = COL["Line total"]
    heads = [i + 1 for i, v in enumerate(col) if isinstance(v, str) and v.endswith(")") and i >= HEADER_ROW]
    for h in heads:
        assert ws.cell(row=h, column=lt).value.startswith("=SUM(")
    total = ws.cell(row=ws.max_row, column=lt).value
    assert total == "=SUM(" + ",".join(f"L{h}" for h in heads) + ")"


def test_line_total_formula_follows_min_order_and_rejected_is_zero():
    ws = sheet()
    col = first_col(ws)
    j = col.index("J1") + 1
    assert ws.cell(row=j, column=COL["Line total"]).value == f'=IF(K{j}="","",MAX(B{j},J{j})*K{j})'
    rej = col.index("R2") + 1
    assert ws.cell(row=rej, column=COL["Line total"]).value == 0
    assert ws.cell(row=rej, column=1).font.strike


def test_problem_cells_are_flagged():
    ws = sheet()
    col = first_col(ws)
    fill = lambda r, h: ws.cell(row=col.index(r) + 1, column=COL[h]).fill.fgColor.rgb[-6:]
    assert fill("D1", "Lifecycle") == "FFC7CE"  # obsolete
    assert fill("C1 C2", "Lifecycle") != "FFC7CE"  # active
    assert fill("R1", "Stock") == "FFEB9C"  # 0 in stock, need 1
    assert fill("R1", "Unit price") == "FFEB9C"  # no price
    assert fill("J1", "MPN") == "FFEB9C"  # no MPN
    assert fill("C1 C2", "MPN") != "FFEB9C"


def test_user_text_never_becomes_a_formula_and_links_are_clickable():
    ws = sheet()
    col = first_col(ws)
    mpn = ws.cell(row=col.index("R2") + 1, column=COL["MPN"])
    assert mpn.value == '=HYPERLINK("x")' and mpn.data_type == "s"
    ds = ws.cell(row=col.index("Unplaced (1)") + 2, column=COL["Datasheet"])
    assert ds.value == "https://example.com/ds.pdf" and ds.hyperlink.target == ds.value


def test_widths_fit_content_with_caps():
    ws = sheet([row("a", ["U1"], value="x" * 200, footprint="Package_SO:SOIC-8")])
    width = lambda h: ws.column_dimensions[ws.cell(row=HEADER_ROW, column=COL[h]).column_letter].width
    assert width("Value") == 24  # capped
    assert width("Footprint") == len("Package_SO:SOIC-8") + 2


def test_empty_bom_still_exports():
    ws = sheet([])
    assert first_col(ws)[-1] == "TOTAL"


def test_filenames():
    assert export_filename("my board", "xlsx", date(2026, 11, 19)) == "my board-BOM-2026-11-19.xlsx"
    assert export_filename('a/b:c"', "csv", date(2026, 1, 2)) == "a_b_c_-BOM-2026-01-02.csv"
    h = content_disposition("Ωhm-BOM-2026-11-19.csv")
    assert 'filename="_hm-BOM-2026-11-19.csv"' in h and "filename*=UTF-8''%CE%A9hm" in h
