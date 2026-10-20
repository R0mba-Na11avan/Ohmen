"""Datasheet tools for agents (the chat agent and the review team).

The three steps an engineer takes with a datasheet: get it, find the right
pages, read them. Every page an agent reads is recorded in its `ledger`, so a
finding can only cite a page that was actually opened (see the evidence gate).
"""
from __future__ import annotations

from pathlib import Path

from pcb_assistant.datasheets.identity import PartRef, part_for
from pcb_assistant.datasheets.store import DatasheetLibrary
from pcb_assistant.ir.models import Design

MAX_PAGES_PER_READ = 3  # every page read is re-sent on each later turn
MAX_PDF_PAGES = 2  # page images are expensive; text covers most reading
MAX_CHARS_PER_PAGE = 6000
SEARCH_HITS = 4


def strip_running_lines(pages: list[str]) -> list[str]:
    """Drop page headers/footers: lines that repeat on many pages of the document
    (doc title, revision code, 'www.ti.com', legal footer). They say nothing and
    would be paid for on every page an agent reads."""
    from collections import Counter

    if len(pages) < 4:
        return pages
    # Short lines ("GND", "VCC", "1") repeat across pin tables too; only longer
    # lines count as running text.
    seen = Counter(line.strip() for p in pages for line in set(p.splitlines()) if len(line.strip()) >= 10)
    running = {line for line, n in seen.items() if n >= max(3, 0.4 * len(pages))}
    return ["\n".join(line for line in p.splitlines() if line.strip() not in running) for p in pages]


class DatasheetTools:
    def __init__(self, project_dir: str | Path, design: Design | None, supports_pdf: bool = False):
        self.lib = DatasheetLibrary.for_project(project_dir)
        self.design = design
        self.supports_pdf = supports_pdf
        self.ledger: set[tuple[str, int]] = set()  # (part key, page) actually read

    def _part(self, name: str) -> PartRef | str:
        p = part_for(self.design, name)
        if p is None:
            return (
                f"'{name}' isn't a part with a datasheet on this board (passives and "
                "generic symbols have none). Use a ref like U3 or a part number."
            )
        return p

    def find(self, part: str) -> str:
        p = self._part(part)
        if isinstance(p, str):
            return p
        e = self.lib.find(p)
        if e.status == "ok":
            return f"{p.key} ({', '.join(p.refs)}): datasheet stored at {e.file}, {e.pages} pages (via {e.source})."
        why = "; ".join(f"{a.source}: {a.error}" for a in e.tried[-3:]) or "no source knows this part"
        return (
            f"No datasheet for {p.key} ({', '.join(p.refs)}) — {why}. The engineer can drop "
            "the PDF into the Datasheets tab. Don't make datasheet claims about this part."
        )

    def search(self, part: str, query: str) -> str:
        from pcb_assistant.datasheets.text import search

        p = self._part(part)
        if isinstance(p, str):
            return p
        if self.lib.find(p).status != "ok":
            return self.find(part)
        hits = search(self.lib.pages(p.key), query, k=SEARCH_HITS)
        if not hits:
            return f"Nothing in the {p.key} datasheet matches '{query}'. Try other words (a pin name, a section title)."
        return f"{p.key} datasheet — best pages for '{query}':\n" + "\n".join(
            f"  p.{page}: …{snippet}…" for page, _, snippet in hits
        )

    def read(self, part: str, pages: list[int], as_pdf: bool = False):
        """Page text (or the pages themselves as a PDF, for tables and figures)."""
        p = self._part(part)
        if isinstance(p, str):
            return p
        if self.lib.find(p).status != "ok":
            return self.find(part)
        texts = self.lib.pages(p.key)
        cap = MAX_PDF_PAGES if (as_pdf and self.supports_pdf) else MAX_PAGES_PER_READ
        wanted = [n for n in dict.fromkeys(pages) if 1 <= n <= len(texts)][:cap]
        if not wanted:
            return f"{p.key} datasheet has pages 1–{len(texts)}."
        for n in wanted:
            self.ledger.add((p.key, n))
        if as_pdf and self.supports_pdf:
            from pydantic_ai import BinaryContent, ToolReturn

            from pcb_assistant.datasheets.text import pages_pdf

            subset = pages_pdf(self.lib.pdf_bytes(p.key) or b"", wanted)
            return ToolReturn(
                return_value=f"Attached {p.key} datasheet pages {wanted} as a PDF.",
                content=[BinaryContent(data=subset, media_type="application/pdf")],
            )
        texts = strip_running_lines(texts)
        return "\n\n".join(
            f"=== {p.key} datasheet, page {n} of {len(texts)} ===\n{texts[n - 1][:MAX_CHARS_PER_PAGE]}"
            for n in wanted
        )
