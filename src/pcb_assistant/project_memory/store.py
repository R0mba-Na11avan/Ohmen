"""Persisted per-project memory (`.pcb_assistant/memory.md`).

The agent's internal notepad — never shown in the UI. Capped at `MAX_CHARS` so
it stays cheap to inject into every chat turn; when full, oldest notes go first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pcb_assistant.jsonio import lock_for, read_text, write_text

DEFAULT_REL = Path(".pcb_assistant") / "memory.md"
MAX_CHARS = 2000


class ProjectMemory:
    """Markdown notes the agent keeps for a project, capped at MAX_CHARS."""

    def __init__(self, path: str | Path, max_chars: int = MAX_CHARS):
        self.path = Path(path)
        self.max_chars = max_chars

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "ProjectMemory":
        return cls(Path(project_dir) / DEFAULT_REL)

    def read(self) -> str:
        return read_text(self.path) or ""

    def append(self, note: str, ts: str | None = None) -> int:
        """Add a dated note; evict oldest notes past the cap. Returns notes dropped."""
        note = " ".join(note.split())
        if not note:
            return 0
        ts = ts or datetime.now(timezone.utc).date().isoformat()
        with lock_for(self.path):
            lines = [ln for ln in self.read().splitlines() if ln.strip()]
            lines.append(f"- ({ts}) {note}")
            dropped = 0
            while len(lines) > 1 and len("\n".join(lines)) > self.max_chars:
                lines.pop(0)
                dropped += 1
            self._write("\n".join(lines))
        return dropped

    def rewrite(self, text: str) -> None:
        """Replace the whole notepad (agent consolidating); truncated to the cap."""
        self._write(text.strip())

    def _write(self, text: str) -> None:
        text = text[: self.max_chars]
        write_text(self.path, text + "\n" if text else "")
