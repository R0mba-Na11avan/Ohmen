"""Breadth patterns: clean on the real board, and each new rule can actually fire."""
import copy

from pcb_assistant.ir.models import Component, Design, Net, Node
from pcb_assistant.patterns.runner import load_patterns, run_patterns
from pcb_assistant.reasoning.core import deterministic_review

EXPECTED = {
    "lmr51625-buck", "ap2112-ldo", "tja1051-can",
    "ahct1g125-buffer", "usbc-cc-pulldowns", "esp32s3-wroom",
}


def test_library_covers_can_board_parts():
    assert {p.id for p in load_patterns()} >= EXPECTED


def test_full_library_clean_on_correct_board(design):
    # The founder's real board is correct; no pattern may cry wolf.
    assert deterministic_review(design) == []


def test_still_catches_floating_en(design_floating):
    fs = deterministic_review(design_floating)
    assert any(f.severity == "must-fix" and "floating" in f.title.lower() for f in fs)


# --- pin_on_net rule fires / passes ---

def _buffer(net_of_pin1: str) -> Design:
    return Design(
        components=[Component(ref="U5", value="74AHCT1G125")],
        nets=[
            Net(name=net_of_pin1, nodes=[Node(ref="U5", pin="1")]),
            Net(name="+5V", nodes=[Node(ref="U5", pin="5")]),
        ],
    )


def test_pin_on_net_fires_when_oe_not_grounded():
    fs = run_patterns(_buffer("+5V"))  # OE# tied high instead of GND
    assert any(f.rule_id == "ahct1g125-buffer:oe-grounded" for f in fs)


def test_pin_on_net_clean_when_oe_grounded():
    fs = run_patterns(_buffer("GND"))
    assert not any(f.rule_id == "ahct1g125-buffer:oe-grounded" for f in fs)


# --- component_between fires when a required part is missing (real-board mutation) ---

def test_cc_pulldown_fires_when_removed(design):
    mutated = copy.deepcopy(design)
    for net in mutated.nets:
        if net.name == "Net-(J2-CC1)":
            net.nodes = [n for n in net.nodes if n.ref != "R6"]  # drop the 5.1k
    fs = run_patterns(mutated)
    assert any(f.rule_id == "usbc-cc-pulldowns:cc1-pulldown" for f in fs)
