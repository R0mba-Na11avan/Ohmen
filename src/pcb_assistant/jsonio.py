"""Safe reads and writes for the JSON files in `.pcb_assistant/`.

- Writes go to a temp file that then replaces the target, so a crash mid-write
  never leaves half a file.
- A file that won't parse is moved aside (`bom.json.corrupt-<time>`) and read as
  missing, so one bad file can't lock the user out of a tab.
- `lock_for(path)` is one re-entrant lock per file, shared by every store object
  in the process: the sidecar runs requests and agent tools on several threads.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: str | Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def read_json(path: str | Path) -> Any | None:
    """The parsed file, or None when it's missing or unreadable (moved aside)."""
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as e:
        _quarantine(Path(path), e)
        return None


def read_text(path: str | Path) -> str | None:
    """A text file's contents, or None when it's missing or not UTF-8 (moved aside)."""
    p = Path(path)
    for i in range(10):
        try:
            return p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError as e:
            _quarantine(p, e)
            return None
        except PermissionError:
            # Windows: the file is mid-replace by another writer; that takes a moment.
            if i == 9:
                raise
            time.sleep(0.05)
    return None


def _quarantine(p: Path, err: Exception) -> None:
    aside = p.with_name(f"{p.name}.corrupt-{int(time.time())}")
    try:
        os.replace(p, aside)
        log.warning("%s was unreadable (%s); moved to %s", p, err, aside.name)
    except OSError:
        log.warning("%s is unreadable (%s)", p, err)


def write_json(path: str | Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2))


def write_text(path: str | Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


def write_bytes(path: str | Path, data: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        _replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace(src: str, dst: Path, attempts: int = 10) -> None:
    # Windows refuses to replace a file another process has open (antivirus,
    # cloud sync); those holds are brief, so retry for about a second.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.1)
