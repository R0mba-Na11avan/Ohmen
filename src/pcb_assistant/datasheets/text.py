"""Datasheet page text: extraction (pypdf), keyword search, page subsets.

Text is cached per file hash, so a 100-page datasheet is parsed once. Search is
plain keyword scoring — good enough to land the agent on the right pages, which
it then reads (as text, or as a PDF subset when tables/figures matter).
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter
from pathlib import Path

import logging

from pypdf import PdfReader, PdfWriter

from pcb_assistant.jsonio import read_json, write_json

# pypdf warns loudly about fonts it can't fully decode (e.g. without fontTools);
# the text still extracts, and the warnings bury the sidecar log.
logging.getLogger("pypdf").setLevel(logging.ERROR)


def extract_pages(pdf: bytes) -> list[str]:
    """Text of every page (1 string per page). Unreadable pages come back empty."""
    reader = PdfReader(io.BytesIO(pdf))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # a malformed page shouldn't lose the rest
            out.append("")
    return out


def page_count(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def first_pages_text(pdf: bytes, n: int = 1) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    texts = []
    for page in reader.pages[:n]:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(texts)


def pages_pdf(pdf: bytes, pages: list[int]) -> bytes:
    """A new PDF holding just these 1-based pages (out-of-range ones skipped)."""
    reader = PdfReader(io.BytesIO(pdf))
    writer = PdfWriter()
    for p in pages:
        if 1 <= p <= len(reader.pages):
            writer.add_page(reader.pages[p - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def cached_pages(pdf: bytes, sha: str, cache_dir: Path) -> list[str]:
    """extract_pages, memoized on disk by the file's sha256."""
    path = cache_dir / f"{sha}.json"
    cached = read_json(path)
    if isinstance(cached, list):
        return cached
    pages = extract_pages(pdf)
    write_json(path, pages)
    return pages


_WORD = re.compile(r"[A-Za-z0-9_.+-]+")


def _terms(text: str) -> list[str]:
    return [w.lower().strip(".") for w in _WORD.findall(text) if len(w) > 1]


def search(pages: list[str], query: str, k: int = 5) -> list[tuple[int, float, str]]:
    """Best-matching pages for `query`: [(page_number, score, snippet)], 1-based.

    TF-IDF-ish: rare terms (e.g. 'EN', 'VCC') weigh more than common ones; the
    whole phrase appearing verbatim gets a bonus.
    """
    q = _terms(query)
    if not q or not pages:
        return []
    docs = [Counter(_terms(p)) for p in pages]
    n = len(docs)
    df = {t: sum(1 for d in docs if t in d) for t in set(q)}
    phrase = query.lower().strip()
    scored = []
    for i, d in enumerate(docs):
        score = sum(
            (1 + math.log(d[t])) * math.log(1 + n / df[t]) for t in set(q) if d.get(t)
        )
        if len(phrase) > 3 and phrase in pages[i].lower():
            score *= 1.5
        if score > 0:
            scored.append((i + 1, round(score, 2), _snippet(pages[i], q)))
    scored.sort(key=lambda s: -s[1])
    return scored[:k]


def _snippet(text: str, terms: list[str], width: int = 240) -> str:
    low = text.lower()
    hits = [low.find(t) for t in terms if low.find(t) >= 0]
    at = min(hits) if hits else 0
    start = max(0, at - width // 3)
    return " ".join(text[start : start + width].split())


def normalize(s: str) -> str:
    """For fuzzy matching: lowercase, collapse all whitespace/hyphenation."""
    return re.sub(r"[\s\-‐‑–]+", "", s.lower())
