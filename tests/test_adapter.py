"""KiCad netlist adapter: reproduces the spike IR as typed models."""
from pcb_assistant.ir.models import Design


def test_counts_match_spike(design: Design):
    assert len(design.components) == 48
    assert len(design.nets) == 55


def test_meta_records_provenance(design: Design):
    assert design.meta.get("source") == "kicad_netlist"
    assert "netlist.xml" in design.meta.get("source_file", "")


def test_fields_captured(design: Design):
    # Every component's Datasheet field (when present) is pulled up.
    u1 = design.component("U1")
    assert u1 is not None
    assert u1.value and "51625" in u1.value


def test_canh_canl_resolve(design: Design):
    names = " ".join(n.name.upper() for n in design.nets)
    assert "CANH" in names and "CANL" in names
