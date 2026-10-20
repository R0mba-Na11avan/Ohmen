"""KiCad ERC/DRC -> Findings. Parsers run on CAN-board reports captured from
kicad-cli 10.0.5. The live run needs kicad-cli plus a real project: set
PCB_LIVE_PROJECT to a KiCad project folder with ERC/DRC violations."""
import json
import os
from pathlib import Path

import pytest

from pcb_assistant.adapters import kicad_checks
from pcb_assistant.adapters.kicad_checks import (
    find_project_pcb,
    parse_drc_json,
    parse_erc_json,
)
from pcb_assistant.adapters.kicad_netlist import _locate_kicad_cli
from pcb_assistant.verify.verifier import apply_citation_gate

FIX = Path(__file__).parent / "fixtures"
LIVE_PROJECT = os.environ.get("PCB_LIVE_PROJECT")


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_erc_groups_by_rule_and_part_and_skips_power_symbols():
    findings = parse_erc_json(_load("erc_can.json"))
    refs = {f.component_ref for f in findings}
    # 9 pin_to_pin warnings on CR1/U1/U2 collapse to one finding per part;
    # #FLG/#PWR power symbols are never the component.
    assert refs == {"CR1", "U1", "U2"}
    assert all(f.source == "erc" and f.rule_id == "erc:pin_to_pin" for f in findings)
    assert all(f.severity == "verify" for f in findings)  # KiCad warnings
    u2 = next(f for f in findings if f.component_ref == "U2")
    assert "(×4)" in u2.title and "Symbol U2 Pin 3 [EN" in u2.detail


def test_drc_severity_mapping():
    findings = parse_drc_json(_load("drc_can.json"))
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f.rule_id, []).append(f)
    hole = by_rule["drc:hole_clearance"][0]
    assert hole.severity == "must-fix" and hole.component_ref == "J2" and "(×4)" in hole.title
    assert all(f.severity == "cosmetic" for f in by_rule["drc:silk_over_copper"])
    assert by_rule["drc:via_dangling"][0].severity == "verify"
    unconnected = {f.component_ref for f in by_rule["drc:unconnected_items"]}
    assert {"U5", "C11"} <= unconnected
    assert all(f.net == "GND" for f in by_rule["drc:unconnected_items"])


def test_kicad_findings_survive_the_citation_gate():
    findings = parse_drc_json(_load("drc_can.json"))
    gated = apply_citation_gate(findings)
    assert [f.severity for f in gated] == [f.severity for f in findings]
    assert all(f.citation.startswith("KiCad DRC rule") for f in gated)


def test_mute_key_is_stable_per_rule_and_part():
    a = parse_erc_json(_load("erc_can.json"))
    b = parse_erc_json(_load("erc_can.json"))
    assert [f.mute_key for f in a] == [f.mute_key for f in b]
    assert "erc:pin_to_pin#U1" in {f.mute_key for f in a}


def test_find_project_pcb(tmp_path):
    (tmp_path / "x.kicad_pro").write_text("{}")
    assert find_project_pcb(tmp_path) is None
    (tmp_path / "x.kicad_pcb").write_text("")
    assert find_project_pcb(tmp_path).name == "x.kicad_pcb"


def test_checks_abstain_when_kicad_cli_fails(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("kicad-cli not found")

    monkeypatch.setattr(kicad_checks, "run_erc", boom)
    monkeypatch.setattr(kicad_checks, "run_drc", boom)
    out = kicad_checks.kicad_checks(tmp_path)
    assert [f.rule_id for f in out] == ["erc:not-run", "drc:not-run"]
    assert all(f.severity == "verify" and "unverified" in f.labels for f in out)


@pytest.mark.skipif(
    not LIVE_PROJECT or _locate_kicad_cli() is None,
    reason="needs kicad-cli and PCB_LIVE_PROJECT",
)
def test_live_erc_and_drc():
    erc = kicad_checks.run_erc(Path(LIVE_PROJECT))
    drc = kicad_checks.run_drc(Path(LIVE_PROJECT))
    assert erc and all(f.source == "erc" for f in erc)
    assert drc and all(f.source == "drc" for f in drc)
