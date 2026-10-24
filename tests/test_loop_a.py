"""Loop A: candidate JSON parsing + datasheet fetch validation (no network/API)."""
import pytest

from pcb_assistant.reasoning import retrieval
from pcb_assistant.reasoning.loop_a_parts import _parse_candidates, research_parts
from pcb_assistant.reasoning.retrieval import DatasheetFetchError, fetch_datasheet


def test_parse_plain_array():
    cs = _parse_candidates('[{"mpn":"LMR51625","category":"buck","datasheet_url":"http://x/y.pdf"}]')
    assert cs[0].mpn == "LMR51625" and cs[0].datasheet_url.endswith(".pdf")


def test_parse_fenced_json():
    txt = 'Here you go:\n```json\n[{"mpn":"AP2112","category":"LDO"}]\n```\nthanks'
    cs = _parse_candidates(txt)
    assert cs[0].mpn == "AP2112"


def test_parse_array_in_prose():
    cs = _parse_candidates('sure: [{"mpn":"TJA1051","category":"CAN"}] done')
    assert cs[0].category == "CAN"


class _Resp:
    def __init__(self, content, ctype="application/pdf"):
        self.content = content
        self.headers = {"Content-Type": ctype}
        self.status_code = 200
        self.url = "http://x/y.pdf"
        self.text = content.decode(errors="replace")
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_ok(monkeypatch):
    monkeypatch.setattr(retrieval.requests, "get", lambda *a, **k: _Resp(b"%PDF-1.7 data"))
    assert fetch_datasheet("http://x/y.pdf").startswith(b"%PDF")


def test_fetch_rejects_non_pdf(monkeypatch):
    monkeypatch.setattr(
        retrieval.requests, "get",
        lambda *a, **k: _Resp(b"<html>nope</html>", ctype="text/html"),
    )
    with pytest.raises(DatasheetFetchError, match="no datasheet PDF link"):
        fetch_datasheet("http://x/y.pdf")


# --- DigiKey grounding ---

def _dk(mpn, stock=100, lifecycle="Active", **kw):
    from pcb_assistant.distributors.digikey import DigiKeyPart

    return DigiKeyPart(mpn=mpn, manufacturer="TI", description="IC REG LINEAR 3.3V", stock=stock,
                       lifecycle=lifecycle, digikey_pn=f"{mpn}-ND", unit_price=0.5,
                       parameters={"Output Voltage": "3.3V", "Current - Output": "500mA"},
                       datasheet_url=f"https://ti.com/{mpn.lower()}.pdf", **kw)


class FakeDigiKey:
    def __init__(self, parts):
        self.parts = parts
        self.queries = []

    def keyword_search(self, keywords, limit=10):
        self.queries.append(keywords)
        return self.parts

    def product_details(self, mpn):
        return next((p for p in self.parts if p.mpn == mpn), None)


def test_candidates_come_from_digikey_not_memory(monkeypatch):
    from pcb_assistant.reasoning import loop_a_parts

    replies = iter([
        '["LDO 3.3V 500mA SOT-23"]',
        '[{"mpn": "tlv75733pdbvr", "category": "LDO", "why": "fits"},'
        ' {"mpn": "MADE-UP-123", "category": "LDO", "why": "hallucinated"}]',
    ])
    prompts = []
    monkeypatch.setattr(loop_a_parts, "ask", lambda model, prompt, **k: prompts.append(prompt) or next(replies))
    dk = FakeDigiKey([_dk("TLV75733PDBVR"), _dk("OLD1117", lifecycle="Obsolete"), _dk("NOSTOCK1", stock=0)])
    cs = loop_a_parts.digikey_candidates("3.3V 500mA LDO", "test", 5, dk)
    assert [c.mpn for c in cs] == ["TLV75733PDBVR"]  # the made-up part is dropped
    assert cs[0].datasheet_url == "https://ti.com/tlv75733pdbvr.pdf"
    assert cs[0].digikey == "DigiKey TLV75733PDBVR-ND · 100 in stock · $0.50 · Active"
    assert dk.queries == ["LDO 3.3V 500mA SOT-23"]
    assert "OLD1117" not in prompts[1] and "NOSTOCK1" not in prompts[1]


def test_research_falls_back_to_the_model_without_digikey_results(monkeypatch):
    from pcb_assistant.reasoning import loop_a_parts

    replies = iter(['["LDO"]', '[{"mpn": "AP2112", "category": "LDO", "datasheet_url": "https://x/a.pdf"}]'])
    monkeypatch.setattr(loop_a_parts, "ask", lambda model, prompt, **k: next(replies))
    monkeypatch.setattr(loop_a_parts, "fetch_datasheet",
                        lambda url: (_ for _ in ()).throw(DatasheetFetchError("offline")))
    report = research_parts("LDO", model_spec="test", client=FakeDigiKey([]))
    assert [r.candidate.mpn for r in report.results] == ["AP2112"]
    assert report.results[0].note == "offline"


def test_digikey_agent_tools(tmp_path, monkeypatch, design):
    from pcb_assistant.agent.agent import build_toolset
    from pcb_assistant.distributors import digikey

    dk = FakeDigiKey([_dk("TLV75733PDBVR"), _dk("OLD1117", lifecycle="Not For New Designs")])
    monkeypatch.setattr(digikey, "client_for", lambda project: dk)
    tools = build_toolset("test", tmp_path, design).tools
    out = tools["search_digikey"].function(keywords="LDO 3.3V")
    assert "TLV75733PDBVR (TI)" in out and "Output Voltage: 3.3V" in out and "$0.50" in out
    assert "OLD1117" not in out
    details = tools["digikey_part"].function(mpn="TLV75733PDBVR")
    assert "Current - Output: 500mA" in details and "Datasheet: https://ti.com/" in details
    assert "has no part" in tools["digikey_part"].function(mpn="NOPE")
    monkeypatch.setattr(digikey, "client_for", lambda project: None)
    assert "isn't set up" in tools["search_digikey"].function(keywords="LDO")
