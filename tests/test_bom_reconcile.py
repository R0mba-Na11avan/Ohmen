"""Planned BOM rows merging into schematic rows once the schematic places them."""
import pytest

from pcb_assistant.bom.match import candidates, same_footprint, same_value
from pcb_assistant.bom.models import BomRow
from pcb_assistant.bom.store import BomStore
from pcb_assistant.ir.models import Component, Design


def _design(*parts: tuple[str, str, str, str | None]) -> Design:
    return Design(components=[
        Component(ref=ref, value=value, footprint=fp, mpn=mpn) for ref, value, fp, mpn in parts
    ])


def _planned(**kw) -> BomRow:
    return BomRow(id="", source="llm", **kw)


R0603 = "Resistor_SMD:R_0603_1608Metric"
R0402 = "Resistor_SMD:R_0402_1005Metric"
SON8 = "Package_SON:HVSON-8-1EP_3x3mm"
ESP = "RF_Module:ESP32-S3-WROOM-1"


def test_value_and_footprint_normalise():
    assert same_value("10k", "10K") and same_value("10 kΩ", "10000") and same_value("100n", "100nF")
    assert not same_value("10k", "1k")
    assert same_footprint("0603", R0603) and same_footprint("R_0603_1608Metric", R0603)
    assert not same_footprint("0603", R0402)


def test_same_mpn_merges_and_keeps_the_agents_facts(tmp_path):
    store = BomStore.for_project(tmp_path)
    planned = store.add(_planned(
        mpn="TJA1051T/3", why="CAN transceiver, 3.3 V I/O", datasheet_url="https://x/tja.pdf",
        unit_price=1.2, status="pinned",
    ))
    store.fill_from_design(_design(("U3", "TJA1051T/3", SON8, "TJA1051T/3")))

    rows = store.list()
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "schematic" and row.refdes == ["U3"]
    assert row.why == "CAN transceiver, 3.3 V I/O" and row.unit_price == 1.2
    assert row.datasheet_url == "https://x/tja.pdf" and row.status == "pinned"
    assert store.get(planned.id) is None
    assert store.last_reconcile.merged == [(planned.id, row.id)]


def test_a_more_specific_variant_asks_then_keeps_the_variant(tmp_path):
    store = BomStore.for_project(tmp_path)
    planned = store.add(_planned(mpn="ESP32-S3-WROOM-1-N16R8"))
    design = _design(("U4", "ESP32-S3-WROOM-1", ESP, None))
    store.fill_from_design(design)
    sch = next(r for r in store.list() if r.source == "schematic")
    assert store.get(planned.id).duplicate_of == [sch.id]  # the variant may not fit; ask

    row = store.merge(planned.id, sch.id)
    assert row.mpn == "ESP32-S3-WROOM-1-N16R8"

    store.fill_from_design(design)  # a later fill doesn't fall back to the base name
    assert store.list()[0].mpn == "ESP32-S3-WROOM-1-N16R8"


def test_passive_by_value_and_case_size(tmp_path):
    store = BomStore.for_project(tmp_path)
    store.add(_planned(value="10K", footprint="0603", why="I2C pull-ups"))
    store.fill_from_design(_design(("R1", "10k", R0603, None), ("R2", "10k", R0603, None),
                                   ("R3", "10k", R0402, None)))
    rows = {tuple(r.refdes): r for r in store.list()}
    assert set(rows) == {("R1", "R2"), ("R3",)}
    assert rows[("R1", "R2")].why == "I2C pull-ups" and rows[("R1", "R2")].qty == 2


def test_ambiguous_match_is_tagged_not_merged(tmp_path):
    store = BomStore.for_project(tmp_path)
    planned = store.add(_planned(value="10k"))  # no footprint: 0603 or 0402?
    store.fill_from_design(_design(("R1", "10k", R0603, None), ("R3", "10k", R0402, None)))

    row = store.get(planned.id)
    assert row is not None and len(row.duplicate_of) == 2
    assert store.last_reconcile.ambiguous == {planned.id: row.duplicate_of}

    kept = store.merge(planned.id, row.duplicate_of[0])
    assert store.get(planned.id) is None and kept.duplicate_of == []


def test_unplaced_and_rejected_rows_stay(tmp_path):
    store = BomStore.for_project(tmp_path)
    coming = store.add(_planned(mpn="LMR51625XDDCR"))
    dropped = store.add(_planned(mpn="TJA1051T/3", status="rejected"))
    store.fill_from_design(_design(("U3", "TJA1051T/3", SON8, "TJA1051T/3")))
    assert store.get(coming.id).duplicate_of == []
    assert store.get(dropped.id) is not None  # a rejected pick isn't revived by merging
    assert store.last_reconcile.unplaced == [coming.id]


def test_a_different_mpn_is_only_a_maybe():
    placed = BomRow(id="sch:1", source="schematic", value="100n", mpn="C0603C104M5RACTU",
                    footprint="Capacitor_SMD:C_0603_1608Metric", refdes=["C1"])
    hits, sure = candidates(_planned(value="100nF", footprint="0603", mpn="GRM188R71H104KA93D"), [placed])
    assert hits == [placed] and not sure

    esp = BomRow(id="sch:2", source="schematic", value="ESP32-S3-WROOM-1",
                 mpn="ESP32-S3-WROOM-1-N4", refdes=["U4"])
    hits, sure = candidates(_planned(mpn="ESP32-S3-WROOM-1-N16R8"), [esp])
    assert hits == [esp] and not sure  # same module, different flash: a person decides


def test_value_names_the_part_when_mpns_differ():
    tvs = BomRow(id="sch:3", source="schematic", value="SMBJ30A", mpn="P6SMBJ30A", refdes=["CR1"])
    hits, sure = candidates(_planned(mpn="SMBJ30A"), [tvs])
    assert hits == [tvs] and not sure


def test_merging_a_different_part_keeps_the_placed_parts_facts(tmp_path):
    store = BomStore.for_project(tmp_path)
    store.fill_from_design(_design(("C1", "100n", "Capacitor_SMD:C_0603_1608Metric", "C0603C104M5RACTU")))
    sch = store.list()[0]
    planned = store.add(_planned(mpn="GRM188R71H104KA93D", value="100nF", footprint="0603",
                                 why="X7R decoupling", unit_price=0.05, distributor_pn="490-1-ND"))
    store.reconcile()
    assert store.get(planned.id).duplicate_of == [sch.id]

    kept = store.merge(planned.id, sch.id)
    assert kept.mpn == "C0603C104M5RACTU"
    assert kept.unit_price is None and kept.distributor_pn is None  # those were the other part's
    assert kept.why == "X7R decoupling (planned as GRM188R71H104KA93D)"


def test_keep_separate_stops_the_suggestion(tmp_path):
    store = BomStore.for_project(tmp_path)
    design = _design(("U2", "AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5", "AP2112K-3.3TRG1"))
    store.fill_from_design(design)
    # An alternative LDO, not the one placed.
    ldo = store.add(_planned(mpn="TLV75533PDBVR", value="AP2112K-3.3"))
    store.reconcile()
    assert store.get(ldo.id).duplicate_of

    store.keep_separate(ldo.id)
    store.fill_from_design(design)
    row = store.get(ldo.id)
    assert row.duplicate_of == [] and row.separate_from
    assert ldo.id in store.last_reconcile.unplaced


def test_merge_rules(tmp_path):
    store = BomStore.for_project(tmp_path)
    store.fill_from_design(_design(("U3", "TJA1051T/3", SON8, "TJA1051T/3")))
    sch = store.list()[0]
    a = store.add(_planned(mpn="LMR51625", qty=1))
    b = store.add(_planned(mpn="LMR51625XDDCR", qty=2, unit_price=0.9))

    with pytest.raises(ValueError):
        store.merge(sch.id, a.id)  # schematic rows can't be folded away
    with pytest.raises(KeyError):
        store.merge("nope", a.id)

    kept = store.merge(b.id, a.id)  # planned duplicates of each other
    assert kept.mpn == "LMR51625XDDCR" and kept.qty == 2 and kept.unit_price == 0.9
    assert store.get(b.id) is None


def test_agent_tools_merge_and_report(tmp_path):
    from pcb_assistant.agent.agent import BOM_MUTATING_TOOLS, build_toolset

    design = _design(("U3", "TJA1051T/3", SON8, "TJA1051T/3"), ("R1", "10k", R0603, None),
                     ("R3", "10k", R0402, None))
    tools = build_toolset("test", tmp_path, design).tools
    call = lambda name, **kw: tools[name].function(**kw)  # noqa: E731

    assert "merge_bom_rows" in BOM_MUTATING_TOOLS
    call("fill_bom_from_schematic")
    assert "already on the board (U3)" in call("add_bom_row", mpn="TJA1051T/3", why="CAN")
    maybe = call("add_bom_row", mpn="", value="10k")
    assert "may already be" in maybe

    listing = call("get_bom")
    assert "maybe already" in listing
    store = BomStore.for_project(tmp_path)
    planned = next(r for r in store.list() if r.source == "llm")
    target = planned.duplicate_of[0]
    assert call("keep_bom_row_separate", id=planned.id).startswith("Kept")
    assert BomStore.for_project(tmp_path).get(planned.id).duplicate_of == []
    assert call("merge_bom_rows", source_id=planned.id, into_id=target).startswith("Merged")
    assert all(r.source == "schematic" for r in BomStore.for_project(tmp_path).list())
