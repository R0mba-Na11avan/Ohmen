"""BOM store: fill-from-schematic grouping, persistence, mutations."""
from pcb_assistant.bom.models import BomRow
from pcb_assistant.bom.store import BomStore, refdes_key


def test_fill_from_design_groups_and_persists(design, tmp_path):
    store = BomStore.for_project(tmp_path)
    n = store.fill_from_design(design)
    rows = store.list()

    assert n == len(rows)
    on_bom = [c for c in design.components if c.in_bom and not c.dnp]
    assert sum(r.qty for r in rows) == len(on_bom)  # every part accounted for
    assert len(rows) <= len(on_bom)  # grouping collapses duplicates
    assert all(r.source == "schematic" for r in rows)

    reloaded = BomStore.for_project(tmp_path)  # survives a reload
    assert {r.id for r in reloaded.list()} == {r.id for r in rows}


def test_fill_is_idempotent(design, tmp_path):
    store = BomStore.for_project(tmp_path)
    n1 = store.fill_from_design(design)
    n2 = store.fill_from_design(design)
    assert n1 == n2 == len(store.list())  # re-fill updates in place, no duplicates


def test_changed_footprint_moves_the_part_to_a_new_row(design, tmp_path):
    store = BomStore.for_project(tmp_path)
    store.fill_from_design(design)
    manual = store.add(BomRow(id="", mpn="EXTRA", source="manual"))
    c = design.components[0]
    old = next(r for r in store.list() if c.ref in r.refdes)

    fixed = design.model_copy(deep=True)
    fixed.components[0].footprint = "Inductor_SMD:L_1210_3225Metric"
    store.fill_from_design(fixed)

    rows = [r for r in store.list() if c.ref in r.refdes]
    assert len(rows) == 1 and rows[0].footprint == "Inductor_SMD:L_1210_3225Metric"
    assert old.qty > 1 or store.get(old.id) is None  # a row left with no parts is gone
    assert store.get(manual.id) is not None  # manual rows are never pruned
    on_bom = [c for c in design.components if c.in_bom and not c.dnp]
    assert sum(r.qty for r in store.list() if r.source == "schematic") == len(on_bom)


def test_excluded_and_dnp_parts_stay_off_the_bom(design, tmp_path):
    assert any(not c.in_bom for c in design.components)  # the fixture's mounting hole
    marked = design.model_copy(deep=True)
    marked.components[0].dnp = True
    store = BomStore.for_project(tmp_path)
    store.fill_from_design(marked)
    on_bom = {ref for r in store.list() for ref in r.refdes}
    off = {c.ref for c in marked.components if not c.in_bom or c.dnp}
    assert marked.components[0].ref in off and not on_bom & off


def test_rows_list_in_natural_refdes_order(tmp_path):
    store = BomStore.for_project(tmp_path)
    for refs in (["R10"], ["C1"], [], ["R2", "R11"], ["U1"]):
        store.add(BomRow(id="", refdes=refs, source="manual"))
    assert [r.refdes for r in store.list()] == [["C1"], ["R2", "R11"], ["R10"], ["U1"], []]


def test_refdes_within_a_row_are_natural_ordered(design, tmp_path):
    store = BomStore.for_project(tmp_path)
    store.fill_from_design(design)
    for r in store.list():
        assert r.refdes == sorted(r.refdes, key=refdes_key)


def test_add_update_remove(tmp_path):
    store = BomStore.for_project(tmp_path)
    row = store.add(BomRow(id="", mpn="TESTPART", source="manual"))
    assert row.id

    store.update(row.id, status="pinned", mpn="TESTPART2")
    got = store.get(row.id)
    assert got.status == "pinned" and got.mpn == "TESTPART2"

    store.remove(row.id)
    assert store.get(row.id) is None


def test_same_value_and_footprint_with_different_mpns_are_separate_rows(tmp_path):
    from pcb_assistant.ir.models import Component, Design

    fp = "Resistor_SMD:R_0603_1608Metric"
    design = Design(components=[
        Component(ref="R1", value="10k", footprint=fp, mpn="RC0603FR-0710KL"),
        Component(ref="R2", value="10k", footprint=fp, mpn="ERJ-3EKF1002V"),
        Component(ref="R3", value="10k", footprint=fp, mpn="ERJ-3EKF1002V"),
        Component(ref="R4", value="10k", footprint=fp),
    ])
    store = BomStore.for_project(tmp_path)
    store.fill_from_design(design)
    rows = {tuple(r.refdes): r.mpn for r in store.list()}
    assert rows == {("R1",): "RC0603FR-0710KL", ("R2", "R3"): "ERJ-3EKF1002V", ("R4",): None}
