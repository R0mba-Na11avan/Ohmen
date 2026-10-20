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
