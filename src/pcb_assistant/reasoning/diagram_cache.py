"""The project's most-recent generated diagram (`.pcb_assistant/diagram.json`).

Diagrams cost an LLM call — persist the last one so it survives an app restart
and doesn't need regenerating to look at.
"""
from __future__ import annotations

import time
from pathlib import Path

from pcb_assistant.jsonio import lock_for, read_json, read_text, write_json, write_text

DEFAULT_REL = Path(".pcb_assistant") / "diagram.json"
# The diagram a regenerate replaced — one-step undo for a bad redraw.
PREVIOUS_REL = Path(".pcb_assistant") / "diagram.prev.json"


def _path(project_dir: str | Path) -> Path:
    return Path(project_dir) / DEFAULT_REL


def save_diagram(
    project_dir: str | Path,
    purpose: str,
    mermaid: str,
    model: str,
    note: str = "",
    inputs: str = "",
) -> None:
    p = _path(project_dir)
    payload = {
        "purpose": purpose, "mermaid": mermaid, "model": model, "note": note,
        "inputs": inputs, "ts": time.time(),
    }
    with lock_for(p):
        current = read_text(p)
        if current:  # keep what this replaces, so a bad redraw can be undone
            write_text(Path(project_dir) / PREVIOUS_REL, current)
        write_json(p, payload)


def has_previous(project_dir: str | Path) -> bool:
    return (Path(project_dir) / PREVIOUS_REL).exists()


def revert_diagram(project_dir: str | Path) -> bool:
    """Swap the current diagram with the one it replaced (undo, and redo)."""
    cur, prev = _path(project_dir), Path(project_dir) / PREVIOUS_REL
    with lock_for(cur):
        older = read_text(prev)
        if not older:
            return False
        newer = read_text(cur)
        write_text(cur, older)
        if newer:
            write_text(prev, newer)
        else:
            prev.unlink(missing_ok=True)
    return True


def load_diagram(project_dir: str | Path) -> dict:
    data = read_json(_path(project_dir))
    if not isinstance(data, dict):
        return {"purpose": "", "mermaid": "", "model": "", "note": "", "ts": 0.0}
    return data
