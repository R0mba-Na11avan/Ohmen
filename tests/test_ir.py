"""Typed IR: validation at the seam, roundtrip, and the lookup helpers."""
import pytest
from pydantic import ValidationError

from pcb_assistant.ir.models import Component, Design, Net, Node


def test_component_requires_ref():
    with pytest.raises(ValidationError):
        Component()  # ref is mandatory


def test_roundtrip_preserves_data(design: Design):
    again = Design.model_validate_json(design.model_dump_json())
    assert again == design


def test_ingests_legacy_spike_json(design_correct: Design):
    # The spike JSON is the old untyped shape; the typed IR must still ingest it.
    assert len(design_correct.components) > 0
    assert len(design_correct.nets) > 0


def test_component_lookup(design: Design):
    assert design.component("U1") is not None
    assert design.component("NOPE") is None


def test_find_by_value_substring(design: Design):
    buck = design.find_components("51625")
    assert [c.ref for c in buck] == ["U1"]


def test_net_of_pin_is_ground_truth(design: Design):
    # Buck EN (U1 pin 2) is tied to +24V on the correct board.
    net = design.net_of_pin("U1", "2")
    assert net is not None and net.name == "+24V"


def test_net_of_pin_unconnected_returns_none():
    d = Design(components=[Component(ref="U1")], nets=[])
    assert d.net_of_pin("U1", "2") is None


def test_floating_en_is_isolated(design_floating: Design):
    # In the injected-bug IR, EN is floated onto its own unconnected net,
    # so it must NOT share a net with +24V / VIN.
    en_net = design_floating.net_of_pin("U1", "2")
    if en_net is not None:  # bug isolates it onto unconnected-(U1-EN-Pad2)
        assert "24V" not in en_net.name.upper()
        assert not any(n.pinfunction == "VIN" for n in en_net.nodes)
