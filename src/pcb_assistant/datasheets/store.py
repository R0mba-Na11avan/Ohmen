"""The per-project datasheet library.

PDFs live where the designer can see them — `<project>/datasheets/<key>.pdf` —
and the index (where each came from, what was tried) plus the page-text cache
live in `.pcb_assistant/`. `find` is cache-first: a part is fetched once, and a
file the user dropped in by hand always wins.

Documents the agent pulls in while researching (candidate parts that aren't on
the board, app notes by URL) go to the hidden `datasheets/.research/` folder and
stay out of the Datasheets tab. Once such a part is placed or added to the BOM,
`promote` moves its datasheet up into the main folder.
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pcb_assistant.datasheets import fetch, text
from pcb_assistant.datasheets.identity import PartRef
from pcb_assistant.datasheets.sources import RESOLVERS
from pcb_assistant.jsonio import lock_for, read_json, write_bytes, write_json

Status = Literal["ok", "missing", "mismatch"]
Origin = Literal["board", "research"]
RESEARCH_DIR = ".research"


class Attempt(BaseModel):
    source: str
    url: str | None = None
    error: str


class DatasheetEntry(BaseModel):
    key: str
    refs: list[str] = Field(default_factory=list)
    status: Status = "missing"
    file: str | None = None  # relative to the project folder
    source: str | None = None  # schematic | manufacturer | digikey | upload
    url: str | None = None
    sha256: str | None = None
    pages: int = 0
    tried: list[Attempt] = Field(default_factory=list)
    fetched_at: float = 0.0
    origin: Origin = "board"  # research: pulled in by the agent, kept in datasheets/.research/


def _safe_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_") or "part"


def promote_research(project_dir: str | Path, mpn: str) -> None:
    """A part went onto the BOM: move its researched datasheet, if any, into the
    main folder."""
    lib = DatasheetLibrary.for_project(project_dir)
    key = lib.research_key(mpn)
    if key is not None:
        lib.promote(key)


def _hide(path: Path) -> None:
    """Windows ignores the dot prefix: set the hidden attribute too."""
    if sys.platform == "win32":
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)


class DatasheetLibrary:
    def __init__(self, project_dir: str | Path):
        self.project = Path(project_dir)
        self.folder = self.project / "datasheets"
        self.research_folder = self.folder / RESEARCH_DIR
        self.meta = self.project / ".pcb_assistant"
        self.index_path = self.meta / "datasheets.json"
        self.text_cache = self.meta / "datasheet_text"

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "DatasheetLibrary":
        return cls(project_dir)

    # --- index ---

    def _load(self) -> dict[str, DatasheetEntry]:
        raw = read_json(self.index_path)
        if not isinstance(raw, dict):
            return {}
        return {k: DatasheetEntry.model_validate(v) for k, v in raw.get("parts", {}).items()}

    def _save(self, entries: dict[str, DatasheetEntry]) -> None:
        write_json(self.index_path, {"parts": {k: e.model_dump() for k, e in entries.items()}})

    def get(self, key: str) -> DatasheetEntry | None:
        e = self._load().get(key)
        if e and e.status == "ok" and not (self.project / (e.file or "")).is_file():
            e.status = "missing"  # the user deleted the file
        return e

    def list(self, parts: list[PartRef]) -> list[DatasheetEntry]:
        """One entry per part on the board, in board order (unfetched = missing)."""
        out = []
        for p in parts:
            e = self.get(p.key) or DatasheetEntry(key=p.key)
            e.refs = p.refs
            out.append(e)
        return out

    # --- adding files ---

    def _store(self, part: PartRef, pdf: bytes, source: str, url: str | None,
               tried: list[Attempt], origin: Origin = "board") -> DatasheetEntry:
        pages = text.page_count(pdf)
        with lock_for(self.index_path):
            entries = self._load()
            path = self._folder_for(origin) / self._file_name(part.key, entries)
            write_bytes(path, pdf)
            entry = DatasheetEntry(
                key=part.key, refs=part.refs, status="ok",
                file=path.relative_to(self.project).as_posix(),
                source=source, url=url, sha256=hashlib.sha256(pdf).hexdigest(),
                pages=pages, tried=tried, fetched_at=time.time(), origin=origin,
            )
            entries[part.key] = entry
            self._save(entries)
        return entry

    def _folder_for(self, origin: Origin) -> Path:
        if origin == "board":
            return self.folder
        if not self.research_folder.is_dir():
            self.research_folder.mkdir(parents=True)
            _hide(self.research_folder)
        return self.research_folder

    def _file_name(self, key: str, entries: dict[str, DatasheetEntry]) -> str:
        """`<part>.pdf`, unless another part's datasheet already has that name
        ('TJA1051T/3' and 'TJA1051T:3', or names differing only in case)."""
        name = f"{_safe_name(key)}.pdf"
        own = entries.get(key)
        taken = {
            (e.file or "").rsplit("/", 1)[-1].lower()
            for k, e in entries.items() if k != key and e.file
        }
        if own and own.file and own.file.rsplit("/", 1)[-1].lower() not in taken:
            return own.file.rsplit("/", 1)[-1]
        if name.lower() not in taken:
            return name
        return f"{_safe_name(key)}-{hashlib.sha1(key.encode()).hexdigest()[:6]}.pdf"

    def add_upload(self, part: PartRef, pdf: bytes) -> DatasheetEntry:
        """A PDF the user dropped in. Trusted as-is (they know their part)."""
        if not fetch.is_pdf(pdf):
            raise ValueError("that file isn't a PDF")
        try:
            return self._store(part, pdf, "upload", None, [])
        except ValueError:
            raise
        except Exception as e:  # pypdf on an encrypted or broken file
            raise ValueError(f"couldn't read that PDF ({e})") from e

    # --- fetching ---

    def find(self, part: PartRef, refresh: bool = False, origin: Origin = "board") -> DatasheetEntry:
        """The part's datasheet: cached, else tried source by source until one fits.
        A board part whose datasheet was found while researching is promoted."""
        cached = self.get(part.key)
        if cached and cached.status == "ok" and not refresh:
            if cached.origin == "research" and origin == "board":
                return self.promote(part.key) or cached
            return cached

        tried: list[Attempt] = []
        mismatched: tuple[str, str, bytes] | None = None
        seen: set[str] = set()
        for source, resolve in RESOLVERS:
            try:
                urls = resolve(part, self.project)
            except Exception as e:  # a source being down never stops the others
                tried.append(Attempt(source=source, error=str(e)))
                continue
            for url in urls:
                if url in seen:
                    continue
                seen.add(url)
                try:
                    pdf, final = fetch.download(url, part.key)
                except fetch.FetchError as e:
                    tried.append(Attempt(source=source, url=url, error=str(e)))
                    continue
                if not fetch.matches_part(pdf, part.key):
                    tried.append(Attempt(
                        source=source, url=url,
                        error=f"wrong document: '{part.key}' isn't on page 1",
                    ))
                    mismatched = mismatched or (source, final, pdf)
                    continue
                return self._store(part, pdf, source, final, tried, origin)

        # Some vendors (NXP, onsemi) answer scripts with 404/403 but serve a real
        # browser fine: retry the URLs that failed outright through the app's one.
        if fetch.browser_available():
            for a in [a for a in tried if a.url and not a.error.startswith("wrong document")]:
                try:
                    pdf = fetch.browser_fetch(a.url)
                except fetch.FetchError as e:
                    tried.append(Attempt(source=f"{a.source}+browser", url=a.url, error=str(e)))
                    continue
                if fetch.is_pdf(pdf) and fetch.matches_part(pdf, part.key):
                    return self._store(part, pdf, a.source, a.url, tried, origin)
                tried.append(Attempt(source=f"{a.source}+browser", url=a.url,
                                     error="browser got no matching PDF"))

        entry = DatasheetEntry(
            key=part.key, refs=part.refs,
            status="mismatch" if mismatched else "missing", tried=tried, origin=origin,
        )
        with lock_for(self.index_path):
            entries = self._load()
            entries[part.key] = entry
            self._save(entries)
        return entry

    def add_document(self, key: str, url: str, guard=None) -> DatasheetEntry:
        """Any PDF by URL (an app note, a reference design, a datasheet the
        resolvers can't find), kept for research under `key`. `guard(url)` raises
        ValueError for a URL that mustn't be used, including where it redirected."""
        try:
            pdf, final = fetch.download(url, key)
        except fetch.FetchError:
            if not fetch.browser_available():
                raise
            pdf, final = fetch.browser_fetch(url), url
        if guard is not None:
            guard(final)
        if not fetch.is_pdf(pdf):
            raise fetch.FetchError(f"{url}: not a PDF")
        return self._store(PartRef(key=key), pdf, "url", final, [], "research")

    def promote(self, key: str) -> DatasheetEntry | None:
        """Move a researched datasheet into the main folder (the part is now on
        the board or the BOM). None if there's no researched copy."""
        with lock_for(self.index_path):
            entries = self._load()
            e = entries.get(key)
            if not e or e.origin != "research":
                return None
            if e.status == "ok" and e.file and (self.project / e.file).is_file():
                src = self.project / e.file
                dst = self.folder / src.name
                if dst.exists():  # the main folder has its own file by that name
                    dst = self.folder / f"{src.stem}-{hashlib.sha1(key.encode()).hexdigest()[:6]}.pdf"
                src.replace(dst)
                e.file = dst.relative_to(self.project).as_posix()
            e.origin = "board"
            self._save(entries)
            return e

    def research_key(self, name: str) -> str | None:
        """The stored key a name refers to among researched documents (any case)."""
        n = name.strip().upper()
        return next((k for k, e in self._load().items() if e.origin == "research" and k.upper() == n), None)

    # --- reading ---

    def pdf_bytes(self, key: str) -> bytes | None:
        e = self.get(key)
        if not e or e.status != "ok" or not e.file:
            return None
        return (self.project / e.file).read_bytes()

    def pages(self, key: str) -> list[str]:
        """Page texts (cached by file hash)."""
        e = self.get(key)
        pdf = self.pdf_bytes(key)
        if pdf is None or e is None:
            return []
        return text.cached_pages(pdf, e.sha256 or hashlib.sha256(pdf).hexdigest(), self.text_cache)
