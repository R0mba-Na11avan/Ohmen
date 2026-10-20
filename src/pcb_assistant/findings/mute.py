"""ERC-style per-project mute/exclusions.

Users dismiss false findings; the mute persists per project (like KiCad's ERC
exclusions) and suppresses that same issue on the same target next run. Mute-rate
is also the negative-feedback signal that later gates provisional-pattern
promotion — so mutes are recorded with a reason and kept, not deleted.
"""
from __future__ import annotations

from pathlib import Path

from pcb_assistant.findings.models import Finding
from pcb_assistant.jsonio import lock_for, read_json, write_json

DEFAULT_REL = Path(".pcb_assistant") / "mutes.json"


class MuteStore:
    """A persisted set of muted `mute_key`s with reasons, per project."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._muted: dict[str, str] = {}
        self._load()

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "MuteStore":
        return cls(Path(project_dir) / DEFAULT_REL)

    def _load(self) -> None:
        data = read_json(self.path) or {}
        self._muted = dict(data.get("muted", {}))

    def _save(self) -> None:
        write_json(self.path, {"muted": self._muted})

    def is_muted(self, key: str) -> bool:
        return key in self._muted

    def mute(self, key: str, reason: str = "") -> None:
        with lock_for(self.path):
            self._load()
            self._muted[key] = reason
            self._save()

    def unmute(self, key: str) -> None:
        with lock_for(self.path):
            self._load()
            if self._muted.pop(key, None) is not None:
                self._save()

    def split(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """Partition into (visible, muted) by each finding's mute_key."""
        visible, muted = [], []
        for f in findings:
            (muted if self.is_muted(f.mute_key) else visible).append(f)
        return visible, muted
