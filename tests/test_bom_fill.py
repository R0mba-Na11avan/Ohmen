"""BOM fill from DigiKey: what gets proposed, the filters, and that nothing
touches the BOM until the engineer approves. DigiKey is faked."""
import json

import pytest

from pcb_assistant.bom import fill
from pcb_assistant.bom.store import BomStore
from pcb_assistant.distributors.digikey import parse_product


def product(mpn, *, price=0.10, stock=10_000, status="Active", params=None, desc="", variations=None):
    return {
        "ManufacturerProductNumber": mpn,
        "Manufacturer": {"Name": "Maker"},
        "Description": {"ProductDescription": desc or mpn},
        "QuantityAvailable": stock,
        "ProductStatus": {"Status": status},
        "ProductUrl": f"https://www.digikey.com/{mpn}",
        "Parameters": [{"ParameterText": k, "ValueText": v} for k, v in (params or {}).items()],
        "ProductVariations": variations or [{
            "DigiKeyProductNumber": f"{mpn}CT-ND",
            "PackageType": {"Name": "Cut Tape (CT)"},
            "MinimumOrderQuantity": 1,
            "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": price}],
        }],
    }


class FakeDigiKey:
    def __init__(self, details=None, searches=None):
        self.details = details or {}  # mpn -> product dict
        self.searches = searches or []  # [(substring of keywords, [product dicts])]
        self.keywords: list[str] = []

    def product_details(self, mpn):
        d = self.details.get(mpn)
        return parse_product(d) if d else None

    def keyword_search(self, keywords, limit=10):
        self.keywords.append(keywords)
        for needle, products in self.searches:
            if needle in keywords:
                return [parse_product(p) for p in products]
        return []


def res(mpn, value, tol="±1%", case="0603 (1608 Metric)", **kw):
    return product(mpn, params={"Resistance": value, "Tolerance": tol, "Package / Case": case}, **kw)


def cap(mpn, value, volts, tc="X7R", case="0603 (1608 Metric)", **kw):
    return product(mpn, params={"Capacitance": value, "Voltage - Rated": volts,
                                "Temperature Coefficient": tc, "Package / Case": case}, **kw)


@pytest.fixture
def project(tmp_path, design):
    BomStore.for_project(tmp_path).fill_from_design(design)
    return tmp_path


def row_for(project, ref):
    return next(r for r in BomStore.for_project(project).list() if ref in r.refdes)


def proposal_for(proposals, ref):
    return next(p for p in proposals if ref in p.refdes)


def test_parsing_digikey_values():
    assert fill.dk_number("10 kOhms") == 10_000
    assert fill.dk_number("0.1 µF") == pytest.approx(1e-7)
    assert fill.dk_number("50V") == 50
    assert fill.dk_number("±1%") == 1
    assert fill.dk_capacitance(1e-7) == "0.1UF" and fill.dk_capacitance(47e-6) == "47UF"
    assert fill.dk_capacitance(1e-8) == "10000PF"


def test_divider_resistor_needs_1_percent_value_and_size(project, design):
    client = FakeDigiKey(searches=[("RES 100K", [
        res("CHEAP-5PCT", "100 kOhms", tol="±5%", price=0.01),
        res("WRONG-SIZE", "100 kOhms", case="0805 (2012 Metric)", price=0.02),
        res("WRONG-VALUE", "10 kOhms", price=0.03),
        res("GOOD-PRICEY", "100 kOhms", price=0.10),
        res("GOOD-CHEAP", "100 kOhms", price=0.05),
    ])])
    p = proposal_for(fill.propose(project, design, client), "R1")
    assert p.offer.mpn == "GOOD-CHEAP"
    assert [a.mpn for a in p.alternatives] == ["GOOD-PRICEY"]
    assert "feedback divider" in p.rationale and "1%" in p.rationale


def test_capacitor_rating_is_twice_its_rail(project, design):
    # C2-C4 (1µ, 1206) sit on the 24 V input: 25 V parts are too close.
    client = FakeDigiKey(searches=[("CAP CER 1UF 1206", [
        cap("C-25V", "1 µF", "25V", case="1206 (3216 Metric)", price=0.02),
        cap("C-Y5V", "1 µF", "50V", tc="Y5V", case="1206 (3216 Metric)", price=0.03),
        cap("C-50V", "1 µF", "50V", case="1206 (3216 Metric)", price=0.08),
    ])])
    p = proposal_for(fill.propose(project, design, client), "C2")
    assert p.offer.mpn == "C-50V"
    assert "≥50 V" in p.rationale and "24 V" in p.rationale


def test_reel_only_packaging_is_not_buyable_for_a_prototype(project, design):
    reel = [{"DigiKeyProductNumber": "R-TR-ND", "PackageType": {"Name": "Tape & Reel (TR)"},
             "MinimumOrderQuantity": 5000, "StandardPricing": [{"BreakQuantity": 5000, "UnitPrice": 0.001}]},
            {"DigiKeyProductNumber": "R-CT-ND", "PackageType": {"Name": "Cut Tape (CT)"},
             "MinimumOrderQuantity": 1, "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.10}]}]
    client = FakeDigiKey(searches=[("RES 120 OHM", [res("R120", "120 Ohms", variations=reel)])])
    p = proposal_for(fill.propose(project, design, client), "R8")
    assert p.offer.digikey_pn == "R-CT-ND" and p.offer.unit_price == 0.10
    assert p.offer.packaging == "Cut Tape (CT)"


def test_exact_part_number_and_obsolete_gets_alternatives(project, design):
    client = FakeDigiKey(
        details={"NUP2105L": product("NUP2105L", status="Obsolete")},
        searches=[("NUP2105", [product("NUP2105LT1G", price=0.30)])],
    )
    p = proposal_for(fill.propose(project, design, client), "D3")
    assert p.offer.mpn == "NUP2105L" and p.offer.lifecycle == "Obsolete"
    assert [a.mpn for a in p.alternatives] == ["NUP2105LT1G"]
    assert "Obsolete" in p.rationale


def test_generic_logic_part_matches_the_footprint_package(project, design):
    client = FakeDigiKey(searches=[("74AHCT1G125", [
        product("SN74AHCT1G125DCKR", price=0.20, params={"Package / Case": "6-TSSOP, SC-88, SOT-363"}),
        product("SN74AHCT1G125DBVR", price=0.35, params={"Package / Case": "SOT-23-5"}),
    ])])
    p = proposal_for(fill.propose(project, design, client), "U5")
    assert p.offer.mpn == "SN74AHCT1G125DBVR"
    assert "SOT-23-5" in p.rationale


def test_rows_that_cant_be_looked_up_are_skipped_with_a_reason(project, design):
    out = fill.propose(project, design, FakeDigiKey())
    assert "resistor footprint" in proposal_for(out, "L1").rationale
    assert "generic symbol" in proposal_for(out, "J5").rationale
    assert all("H1" not in p.refdes for p in out)  # excluded from the BOM in KiCad
    assert all(p.status == "skipped" for p in out if p.offer is None)


def test_proposals_never_touch_the_bom_until_approved(project, design):
    before = [r.model_dump() for r in BomStore.for_project(project).list()]
    client = FakeDigiKey(details={"AP2112K-3.3TRG1": product("AP2112K-3.3TRG1", price=0.42)})
    fill.propose(project, design, client)
    assert [r.model_dump() for r in BomStore.for_project(project).list()] == before

    store = fill.ProposalStore(project)
    row = store.approve(row_for(project, "U2").id)
    assert row.mpn == "AP2112K-3.3TRG1" and row.unit_price == 0.42
    assert row.distributor_pn == "AP2112K-3.3TRG1CT-ND" and row.lifecycle == "Active"
    assert next(p for p in store.list() if p.id == row.id).status == "approved"


def test_approving_an_alternative_and_rejecting(project, design):
    client = FakeDigiKey(searches=[("RES 10K", [res("A", "10 kOhms", price=0.01), res("B", "10 kOhms", price=0.02)])])
    fill.propose(project, design, client)
    store = fill.ProposalStore(project)
    r4 = row_for(project, "R4").id
    assert store.approve(r4, choice=1).mpn == "B"
    with pytest.raises(ValueError):
        store.approve(r4, choice=5)
    r9 = row_for(project, "R9").id
    store.reject(r9)
    assert row_for(project, "R9").mpn is None


def test_approve_all_takes_only_pending_offers(project, design):
    client = FakeDigiKey(details={"SMBJ30A": product("SMBJ30A")}, searches=[("RES 330", [res("R330", "330 Ohms")])])
    fill.propose(project, design, client)
    approved = fill.ProposalStore(project).approve_all()
    assert sorted(r.mpn for r in approved) == ["R330", "SMBJ30A"]


def test_pinned_rows_are_left_alone(project, design):
    store = BomStore.for_project(project)
    store.update(row_for(project, "U2").id, status="pinned")
    client = FakeDigiKey(details={"AP2112K-3.3TRG1": product("AP2112K-3.3TRG1")})
    assert not any("U2" in p.refdes for p in fill.propose(project, design, client))


def test_rows_with_a_part_link_are_not_looked_up_again(project, design):
    client = FakeDigiKey(details={"AP2112K-3.3TRG1": product("AP2112K-3.3TRG1")})
    fill.propose(project, design, client)
    u2 = row_for(project, "U2").id
    fill.ProposalStore(project).approve(u2)  # approval copies the DigiKey link onto the row
    r4 = row_for(project, "R4").id
    BomStore.for_project(project).update(r4, product_url="https://www.mouser.com/some-part")
    looked_up = {p.id for p in fill.propose(project, design, client)}
    assert u2 not in looked_up and r4 not in looked_up
    # Naming the row explicitly still re-looks it up.
    assert [p.id for p in fill.propose(project, design, client, [u2])] == [u2]
    # Clearing the link puts it back in the fill.
    BomStore.for_project(project).update(r4, product_url=None)
    assert r4 in {p.id for p in fill.propose(project, design, client)}


def test_refill_from_schematic_keeps_approved_distributor_facts(project, design):
    client = FakeDigiKey(details={"AP2112K-3.3TRG1": product("AP2112K-3.3TRG1")})
    fill.propose(project, design, client)
    fill.ProposalStore(project).approve(row_for(project, "U2").id)
    BomStore.for_project(project).fill_from_design(design)
    row = row_for(project, "U2")
    assert row.distributor_pn == "AP2112K-3.3TRG1CT-ND" and row.lifecycle == "Active"


def test_lifecycle_findings_for_parts_going_away(project, design):
    client = FakeDigiKey(details={"NUP2105L": product("NUP2105L", status="Not For New Designs")})
    fill.propose(project, design, client)
    fill.ProposalStore(project).approve(row_for(project, "D3").id)
    found = fill.lifecycle_findings(project)
    assert len(found) == 1
    f = found[0]
    assert f.severity == "verify" and f.source == "distributor" and f.component_ref == "D3"
    assert "Not For New Designs" in f.title


REEL_ONLY = [{"DigiKeyProductNumber": "X-TR-ND", "PackageType": {"Name": "Tape & Reel (TR)"},
              "MinimumOrderQuantity": 3000, "QuantityAvailableforPackageType": 90000,
              "StandardPricing": [{"BreakQuantity": 3000, "UnitPrice": 0.012}]}]


def test_bulk_only_part_number_is_flagged_with_the_lot_cost(project, design):
    client = FakeDigiKey(
        details={"SMBJ30A": product("SMBJ30A", variations=REEL_ONLY)},
        searches=[("SMBJ30", [product("SMBJ30A-E3", price=0.40)])],
    )
    p = proposal_for(fill.propose(project, design, client), "CR1")
    assert p.offer.bulk_only and p.offer.min_order == 3000 and p.offer.min_order_cost == 36.0
    assert "lots of 3,000 ($36.00)" in p.rationale
    assert [a.mpn for a in p.alternatives] == ["SMBJ30A-E3"]  # one you can buy 1 of


def test_bulk_only_passives_are_proposed_flagged_not_dropped(project, design):
    client = FakeDigiKey(searches=[("RES 330", [res("R330-REEL", "330 Ohms", variations=REEL_ONLY)])])
    p = proposal_for(fill.propose(project, design, client), "R9")
    assert p.status == "pending" and p.offer.bulk_only
    assert "every match is only sold in lots of 3,000" in p.rationale


def test_buyable_beats_bulk_and_digi_reel_is_last_resort(project, design):
    variations = [
        {"DigiKeyProductNumber": "R-DKR-ND", "PackageType": {"Name": "Digi-Reel®"},
         "MinimumOrderQuantity": 1, "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.09}]},
        {"DigiKeyProductNumber": "R-CT-ND", "PackageType": {"Name": "Cut Tape (CT)"},
         "MinimumOrderQuantity": 1, "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.10}]},
    ]
    client = FakeDigiKey(searches=[("RES 330", [
        res("R330-REEL", "330 Ohms", variations=REEL_ONLY),
        res("R330-CT", "330 Ohms", variations=variations),
    ])])
    p = proposal_for(fill.propose(project, design, client), "R9")
    assert p.offer.mpn == "R330-CT" and p.offer.digikey_pn == "R-CT-ND" and not p.offer.bulk_only


def test_offers_carry_the_specs_to_compare_them_by(project, design):
    part = res("R330", "330 Ohms")
    part.update(PhotoUrl="//mm.digikey.com/r330.jpg", Category={"Name": "Chip Resistor - Surface Mount"})
    client = FakeDigiKey(searches=[("RES 330", [part])])
    o = proposal_for(fill.propose(project, design, client), "R9").offer
    assert o.parameters["Resistance"] == "330 Ohms" and o.parameters["Tolerance"] == "±1%"
    assert o.photo_url == "https://mm.digikey.com/r330.jpg"
    assert o.category == "Chip Resistor - Surface Mount"


def test_offer_stock_is_the_packagings_not_the_products(project, design):
    variations = [
        {"DigiKeyProductNumber": "R-TR-ND", "PackageType": {"Name": "Tape & Reel (TR)"},
         "MinimumOrderQuantity": 5000, "QuantityAvailableforPackageType": 900_000,
         "StandardPricing": [{"BreakQuantity": 5000, "UnitPrice": 0.001}]},
        {"DigiKeyProductNumber": "R-CT-ND", "PackageType": {"Name": "Cut Tape (CT)"},
         "MinimumOrderQuantity": 1, "QuantityAvailableforPackageType": 1_200,
         "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.10}]},
    ]
    client = FakeDigiKey(searches=[("RES 330", [res("R330", "330 Ohms", stock=901_200, variations=variations)])])
    o = proposal_for(fill.propose(project, design, client), "R9").offer
    assert o.digikey_pn == "R-CT-ND" and o.stock == 1_200


def test_proposals_saved_before_specs_still_load(project, design):
    client = FakeDigiKey(searches=[("RES 330", [res("R330", "330 Ohms")])])
    fill.propose(project, design, client)
    path = project / fill.PROPOSALS_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    for p in data["proposals"]:
        for o in [p["offer"], *p["alternatives"]]:
            if o:
                for key in ("parameters", "photo_url", "category"):
                    del o[key]
    path.write_text(json.dumps(data), encoding="utf-8")
    p = proposal_for(fill.ProposalStore(project).list(), "R9")
    assert p.offer.mpn == "R330" and p.offer.parameters == {}


def test_proposal_goes_when_its_row_does(project, design):
    client = FakeDigiKey(searches=[("RES 330", [res("R330", "330 Ohms")])])
    fill.propose(project, design, client)
    fixed = design.model_copy(deep=True)
    for c in fixed.components:
        if c.ref == "R9":
            c.footprint = "Resistor_SMD:R_1206_3216Metric"
    BomStore.for_project(project).fill_from_design(fixed)
    assert not any("R9" in p.refdes for p in fill.ProposalStore(project).list())


def test_approved_bulk_part_costs_the_whole_lot(project, design):
    client = FakeDigiKey(details={"SMBJ30A": product("SMBJ30A", variations=REEL_ONLY)})
    fill.propose(project, design, client)
    row = fill.ProposalStore(project).approve(row_for(project, "CR1").id)
    assert row.min_order == 3000 and row.line_total == 36.0
