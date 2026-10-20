"""Datasheet library: part identity, URL sources, download + validation, page
text/search, the agent tools, and the DigiKey client. No network: HTTP is faked."""
import io
import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import NameObject

from pcb_assistant.datasheets import fetch, sources, text
from pcb_assistant.datasheets.identity import (
    PartRef,
    base_mpn,
    looks_like_mpn,
    part_for,
    parts_needing_datasheets,
)
from pcb_assistant.datasheets.store import DatasheetLibrary
from pcb_assistant.datasheets.tools import DatasheetTools


def make_pdf(pages: list[str]) -> bytes:
    """A real PDF whose pages carry `pages` as extractable text."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject

    w = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for body in pages:
        page = w.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})
        })
        safe = body.replace("(", "[").replace(")", "]")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 10 Tf 40 740 Td ({safe}) Tj ET".encode())
        page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class Resp:
    def __init__(self, content=b"", status=200, ctype="application/pdf", url="http://x"):
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.url = url
        self.text = content.decode(errors="replace")
        self.encoding = "utf-8"

    def iter_content(self, size):
        for i in range(0, len(self.content), size):
            yield self.content[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- identity ---

def test_board_parts_and_keys(design):
    keys = {p.key: p.refs for p in parts_needing_datasheets(design)}
    assert keys == {
        "SMBJ30A": ["CR1"], "NUP2105L": ["D3"], "WS2812B": ["D4"], "LMR51625XDDCR": ["U1"],
        "AP2112K-3.3TRG1": ["U2"], "TJA1051TK-3": ["U3"], "ESP32-S3-WROOM-1": ["U4"],
        "74AHCT1G125": ["U5"],
    }


@pytest.mark.parametrize("s,ok", [
    ("TJA1051TK-3", True), ("100n", False), ("4.7k", False), ("10uF", False),
    ("LED", False), ("D_Schottky", False), ("USB_C_Receptacle_USB2.0_14P", False),
    ("https://x/y.pdf", False), ("SMBJ30A", True),
])
def test_looks_like_mpn(s, ok):
    assert looks_like_mpn(s) is ok


@pytest.mark.parametrize("key,base", [
    ("TJA1051TK-3", "TJA1051"), ("LMR51625XDDCR", "LMR51625"), ("AP2112K-3.3TRG1", "AP2112"),
    ("74AHCT1G125", "74AHCT1G125"), ("SN74HC595N", "74HC595"),
])
def test_base_mpn(key, base):
    assert base_mpn(key) == base


def test_part_for_accepts_ref_key_or_family(design):
    assert part_for(design, "U3").key == "TJA1051TK-3"
    assert part_for(design, "tja1051").key == "TJA1051TK-3"
    assert part_for(design, "C1") is None


# --- sources ---

def test_manufacturer_patterns_by_prefix():
    ti = sources.manufacturer_patterns(PartRef(key="74AHCT1G125"), None)
    assert "https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf" in ti
    diodes = sources.manufacturer_patterns(PartRef(key="AP2112K-3.3TRG1"), None)
    assert diodes[0] == "https://www.diodes.com/assets/Datasheets/AP2112.pdf"
    assert sources.manufacturer_patterns(PartRef(key="WS2812B"), None) == []


# --- fetch + validation ---

def test_matches_part_needs_family_on_page_one():
    good = make_pdf(["TJA1051 High-speed CAN transceiver", "pinning"])
    guide = make_pdf(["Logic selection guide", "74AHCT1G125 is on page 2"])
    assert fetch.matches_part(good, "TJA1051TK-3")
    assert not fetch.matches_part(guide, "74AHCT1G125")


def test_download_follows_landing_page_pdf_link(monkeypatch):
    pdf = make_pdf(["LMR51625 buck converter"])
    html = b'<a href="/lit/ds/symlink/lmr51625.pdf">Data sheet</a><a href="/other.pdf">x</a>'

    def get(url, **k):
        if url.endswith("lmr51625.pdf"):
            return Resp(pdf, url=url)
        return Resp(html, ctype="text/html", url="https://www.ti.com/product/LMR51625")

    monkeypatch.setattr(fetch.requests, "get", get)
    data, final = fetch.download("https://www.ti.com/lit/gpn/lmr51625", "LMR51625XDDCR")
    assert data == pdf and final.endswith("/lit/ds/symlink/lmr51625.pdf")


def test_download_reports_blocked_hosts(monkeypatch):
    monkeypatch.setattr(fetch.requests, "get", lambda url, **k: Resp(b"denied", status=403, ctype="text/html"))
    with pytest.raises(fetch.FetchError, match="blocks scripted downloads"):
        fetch.download("https://vendor/x.pdf", "X1234")


# --- library ---

def _board_lib(tmp_path, monkeypatch, responses):
    """A library whose HTTP answers come from `responses` {url: Resp}."""
    def get(url, **k):
        return responses.get(url, Resp(b"nope", status=404, ctype="text/html", url=url))

    monkeypatch.setattr(fetch.requests, "get", get)
    return DatasheetLibrary.for_project(tmp_path)


def test_library_rejects_wrong_document_then_finds_real_one(tmp_path, monkeypatch):
    guide = make_pdf(["Logic guide"])
    real = make_pdf(["SN74AHCT1G125 single bus buffer gate"])
    lib = _board_lib(tmp_path, monkeypatch, {
        "http://ti/guide.pdf": Resp(guide),
        "https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf": Resp(real),
    })
    part = PartRef(key="74AHCT1G125", refs=["U5"], url_hint="http://ti/guide.pdf")
    e = lib.find(part)
    assert e.status == "ok" and e.source == "manufacturer" and e.pages == 1
    assert (tmp_path / "datasheets" / "74AHCT1G125.pdf").read_bytes() == real
    assert any("wrong document" in a.error for a in e.tried)
    # cached: a second find makes no HTTP calls
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: pytest.fail("refetched"))
    assert lib.find(part).status == "ok"


def test_library_missing_part_and_upload(tmp_path, monkeypatch):
    lib = _board_lib(tmp_path, monkeypatch, {})
    part = PartRef(key="SMBJ30A", refs=["CR1"])
    assert lib.find(part).status == "missing"
    up = lib.add_upload(part, make_pdf(["SMBJ series TVS"]))
    assert up.status == "ok" and up.source == "upload"
    with pytest.raises(ValueError):
        lib.add_upload(part, b"not a pdf")


def test_library_retries_blocked_urls_through_browser(tmp_path, monkeypatch):
    pdf = make_pdf(["TJA1051 CAN transceiver"])
    lib = _board_lib(tmp_path, monkeypatch, {})  # every plain GET 404s
    monkeypatch.setenv("PCB_BROWSER_FETCH", "http://127.0.0.1:1/fetch")
    monkeypatch.setattr(fetch, "browser_fetch", lambda url, **k: pdf)
    e = lib.find(PartRef(key="TJA1051TK-3", refs=["U3"], url_hint="https://nxp/TJA1051.pdf"))
    assert e.status == "ok" and e.source == "schematic"


# --- text + tools ---

def test_search_ranks_pages_and_subsets():
    pdf = make_pdf(["Features overview", "Pin description EN enable pin VIN", "Package outline"])
    pages = text.extract_pages(pdf)
    hits = text.search(pages, "EN pin")
    assert hits[0][0] == 2
    assert text.page_count(text.pages_pdf(pdf, [1, 3, 99])) == 2


def test_tools_record_ledger_and_refuse_passives(tmp_path, design):
    lib = DatasheetLibrary.for_project(tmp_path)
    lib.add_upload(part_for(design, "U3"), make_pdf(["TJA1051 CAN", "VCC supply 4.5 to 5.5 V"]))
    t = DatasheetTools(tmp_path, design)
    assert "2 pages" in t.find("U3")
    assert "p.2" in t.search("U3", "VCC supply")
    assert "page 2 of 2" in t.read("U3", [2, 2, 7])
    assert t.ledger == {("TJA1051TK-3", 2)}
    assert "isn't a part with a datasheet" in t.find("C1")


# --- DigiKey ---

class FakeSession:
    def __init__(self, product):
        self.calls = []
        self.product = product

    def post(self, url, data=None, timeout=None):
        self.calls.append(("token", url))
        return type("R", (), {"status_code": 200, "json": lambda s: {"access_token": "t", "expires_in": 600}, "text": ""})()

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls.append((method, url))
        body = {"Product": self.product}
        return type("R", (), {"status_code": 200, "json": lambda s: body, "headers": {}, "text": ""})()


DK_PRODUCT = {
    "ManufacturerProductNumber": "SMBJ30A",
    "Manufacturer": {"Name": "Littelfuse Inc."},
    "Description": {"ProductDescription": "TVS DIODE 30VWM 48.4VC DO214AA"},
    "DatasheetUrl": "//www.littelfuse.com/smbj.pdf",
    "QuantityAvailable": 12000,
    "ProductStatus": {"Status": "Active"},
    "ProductVariations": [{"DigiKeyProductNumber": "SMBJ30ALFCT-ND",
                           "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.41},
                                               {"BreakQuantity": 10, "UnitPrice": 0.30}]}],
}


def test_digikey_parse_and_cache(tmp_path, monkeypatch):
    from pcb_assistant.distributors.digikey import DigiKeyClient
    from pcb_assistant.providers import credentials

    credentials.set_services({"digikey": {"client_id": "id", "client_secret": "s"}})
    session = FakeSession(DK_PRODUCT)
    client = DigiKeyClient(cache_path=tmp_path / "dk.json", session=session)
    part = client.product_details("SMBJ30A")
    assert part.manufacturer == "Littelfuse Inc." and part.lifecycle == "Active"
    assert part.digikey_pn == "SMBJ30ALFCT-ND" and part.price_at(25) == 0.30
    client.product_details("SMBJ30A")  # served from cache
    assert sum(1 for c in session.calls if c[0] == "GET") == 1
    assert json.loads((tmp_path / "dk.json").read_text())


def test_digikey_source_returns_datasheet_url(tmp_path, monkeypatch):
    from pcb_assistant.distributors import digikey
    from pcb_assistant.providers import credentials

    credentials.set_services({"digikey": {"client_id": "id", "client_secret": "s"}})
    monkeypatch.setattr(digikey.requests, "Session", lambda: FakeSession(DK_PRODUCT))
    assert sources.digikey(PartRef(key="SMBJ30A"), tmp_path) == ["https://www.littelfuse.com/smbj.pdf"]


def test_digikey_source_silent_without_key(tmp_path):
    assert sources.digikey(PartRef(key="SMBJ30A"), tmp_path) == []


def test_running_headers_and_footers_are_stripped_but_short_pin_lines_kept():
    from pcb_assistant.datasheets.tools import strip_running_lines

    footer = "SLUSF64B - AUGUST 2024 - REVISED JANUARY 2025"
    pages = [f"Page {i} body text\nGND\n{footer}" for i in range(6)]
    out = strip_running_lines(pages)
    assert all(footer not in p for p in out)
    assert all("GND" in p and "body text" in p for p in out)
    assert strip_running_lines(pages[:3]) == pages[:3]  # too short to tell
