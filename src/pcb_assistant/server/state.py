"""In-memory session state for the sidecar.

This is a single-user desktop app: one design is loaded at a time. `/sync` loads
it; the chat agent and `/findings/autocheck` operate on it; mutes and stored
findings resolve to its project directory (mirroring the CLI's `proj = source.parent`).

The engineer keeps editing in KiCad while the app is open, so `design` re-reads
the schematic whenever a .kicad_sch in the project changed on disk since it was
loaded — a fixed footprint shows up in the next BOM fill, review or chat turn
without re-opening the project.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from pcb_assistant.ir.models import Design

log = logging.getLogger(__name__)


class NoDesignLoaded(RuntimeError):
    """Raised when a route needs a design but none has been synced yet."""


def _fingerprint(source: Path | None, project: Path | None) -> tuple:
    """Name + mtime of every schematic the design is read from (hierarchical
    sheets live beside the root), plus the source itself (netlist / ir.json)."""
    files: set[Path] = set()
    if project is not None and project.is_dir():
        files.update(f for f in project.glob("*.kicad_sch") if not f.name.startswith("_autosave-"))
    if source is not None and source.is_file():
        files.add(source)
    out = []
    for f in sorted(files):
        try:
            out.append((f.name, f.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


class AppState:
    """The one loaded design plus where it came from."""

    def __init__(self) -> None:
        self._design: Design | None = None
        self.source: Path | None = None
        self.project: Path | None = None
        self._seen: tuple = ()
        self._lock = threading.Lock()

    def load(self, design: Design | None, source: Path, project: Path) -> None:
        # Chat history is persisted per-session in the project's ChatStore, not here.
        with self._lock:
            self._design = design
            self.source = source
            self.project = project
            self._seen = _fingerprint(source, project)

    @property
    def design(self) -> Design | None:
        self._refresh_if_changed()
        return self._design

    @design.setter
    def design(self, value: Design | None) -> None:
        self._design = value

    def _refresh_if_changed(self) -> None:
        if self.project is None:
            return
        with self._lock:
            now = _fingerprint(self.source, self.project)
            if now == self._seen:
                return
            self._seen = now  # a half-saved file that won't parse isn't retried every request
            try:
                fresh = self._reload()
            except Exception as e:  # mid-save or broken schematic: keep the last good design
                log.warning("schematic changed but could not be re-read: %s", e)
                return
            if fresh is not None:
                self._design = fresh

    def _reload(self) -> Design | None:
        from pcb_assistant.adapters.kicad_netlist import find_project_schematic
        from pcb_assistant.cli import load_design

        src = self.source
        if src is None or not src.is_file():
            # An empty project that has since gained a schematic.
            try:
                src = find_project_schematic(self.project)
            except FileNotFoundError:
                return None
            self.source = src
        return load_design(src)

    def require_design(self) -> Design:
        design = self.design
        if design is None:
            raise NoDesignLoaded("no design loaded — POST /sync first")
        return design

    def require_project(self) -> Path:
        if self.project is None:
            raise NoDesignLoaded("no design loaded — POST /sync first")
        return self.project


# Module-level singleton; the FastAPI app reads/writes this.
STATE = AppState()
