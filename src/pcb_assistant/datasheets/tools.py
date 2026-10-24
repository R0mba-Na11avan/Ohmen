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


def public_https(url: str) -> None:
    """Raise ValueError unless `url` is https on a host that resolves only to
    public addresses: the agent picks these URLs, so they mustn't reach this
    machine or the local network."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url.strip())
    if u.scheme != "https" or not u.hostname:
        raise ValueError(f"only https:// links can be fetched ({url})")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as e:
        raise ValueError(f"can't resolve {u.hostname}") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise ValueError(f"{u.hostname} is not a public host")


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
        """A board part, else a researched document, else any part number (a
        candidate that isn't on the board; its datasheet is kept for research)."""
        p = part_for(self.design, name)
        if p is not None:
            return p
        key = self.lib.research_key(name)
        if key is not None:
            return PartRef(key=key)
        name = name.strip()
        board_refs = {c.ref.upper() for c in self.design.components} if self.design else set()
        if len(name) < 3 or " " in name or name.upper() in board_refs:
            return (
                f"'{name}' isn't a part with a datasheet on this board (passives and "
                "generic symbols have none). Use a ref like U3 or a part number."
            )
        return PartRef(key=name)

    def _find(self, p: PartRef):
        # a part off the board is research: its datasheet stays out of the tab
        return self.lib.find(p, origin="board" if p.refs else "research")

    @staticmethod
    def _label(p: PartRef) -> str:
        return ", ".join(p.refs) if p.refs else "not on the board"

    def find(self, part: str) -> str:
        p = self._part(part)
        if isinstance(p, str):
            return p
        e = self._find(p)
        if e.status == "ok":
            return f"{p.key} ({self._label(p)}): datasheet stored at {e.file}, {e.pages} pages (via {e.source})."
        why = "; ".join(f"{a.source}: {a.error}" for a in e.tried[-3:]) or "no source knows this part"
        if not p.refs:
            return (
                f"No datasheet for {p.key} — {why}. If you know a URL for it, use fetch_document. "
                "Don't make datasheet claims about this part."
            )
        return (
            f"No datasheet for {p.key} ({self._label(p)}) — {why}. The engineer can drop "
            "the PDF into the Datasheets tab. Don't make datasheet claims about this part."
        )

    def fetch_document(self, url: str, name: str) -> str:
        """Download a PDF by URL into the research folder, readable under `name`."""
        from pcb_assistant.datasheets.fetch import FetchError

        name = name.strip()
        if not name:
            return "Give the document a short name (a part number or e.g. 'TI SLVA477 app note')."
        if part_for(self.design, name) is not None:
            return f"'{name}' is a part on this board: use find_datasheet for it, or pick another name."
        try:
            public_https(url)
            e = self.lib.add_document(name, url, guard=public_https)
        except (ValueError, FetchError) as err:
            return f"Couldn't fetch that document: {err}"
        return (
            f"Stored '{name}' ({e.pages} pages). Read it with search_datasheet / "
            f"read_datasheet_pages using part='{name}'."
        )

    def search(self, part: str, query: str) -> str:
        from pcb_assistant.datasheets.text import search

        p = self._part(part)
        if isinstance(p, str):
            return p
        if self._find(p).status != "ok":
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
        if self._find(p).status != "ok":
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
