"""Persisted per-project chat sessions (`.pcb_assistant/chats.json`).

Each session carries two histories:
- `transcript`: display turns for the UI (`{q, a, tools, images?, error?}`) — what to
  render when the user revisits an old chat.
- `messages`: the pydantic-ai `ModelMessage` list, serialized jsonable, fed back
  as `message_history` so the agent continues where it left off. Turns run on a
  subscription CLI are appended here too (as plain prompt/answer pairs), so a
  later switch back to an API model keeps the whole conversation.
- `engine_session` (optional): the subscription CLI's own session for this chat,
  resumed while the provider and model stay the same.
- `condensed` / `condenses` (optional): the running summary of older turns and
  each time the chat was condensed (agent/condense.py).

Sessions are the *conversations*. The BOM and project memory stay project-global
(shared across every chat) — a quick-question chat still edits the one BOM.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pcb_assistant.jsonio import lock_for, read_json, write_json

DEFAULT_REL = Path(".pcb_assistant") / "chats.json"


def _now() -> float:
    return time.time()


def _title_from(text: str) -> str:
    t = " ".join(text.split())
    if not t:
        return "New chat"
    return t[:80] + "…" if len(t) > 80 else t  # the sidebar is resizable


class ChatStore:
    """Session store for one project. Like BomStore, every edit re-reads the file
    under its lock, so a reply finishing can't undo a chat deleted or created
    while it streamed."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []  # session ids, most-recent first
        self._depth = 0
        self._fresh()

    @classmethod
    def for_project(cls, project_dir: str | Path) -> "ChatStore":
        return cls(Path(project_dir) / DEFAULT_REL)

    def _load(self) -> None:
        data = read_json(self.path) or {}
        self._sessions = data.get("sessions", {})
        self._order = data.get("order", list(self._sessions.keys()))

    def _save(self) -> None:
        write_json(self.path, {"sessions": self._sessions, "order": self._order})

    @contextmanager
    def _edit(self) -> Iterator[None]:
        with lock_for(self.path):
            outer = self._depth == 0
            if outer:
                self._load()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            if outer:
                self._save()

    def _fresh(self) -> None:
        if self._depth == 0:
            with lock_for(self.path):
                self._load()

    def _summary(self, sid: str) -> dict[str, Any]:
        s = self._sessions[sid]
        return {
            "id": sid,
            "title": s["title"],
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
            "turns": len(s.get("transcript", [])),
        }

    def summaries(self) -> list[dict[str, Any]]:
        self._fresh()
        return [self._summary(sid) for sid in self._order if sid in self._sessions]

    def new(self, title: str = "") -> dict[str, Any]:
        sid = uuid.uuid4().hex[:12]
        now = _now()
        with self._edit():
            self._sessions[sid] = {
                "id": sid,
                "title": title or "New chat",
                "created_at": now,
                "updated_at": now,
                "transcript": [],
                "messages": [],
            }
            self._order.insert(0, sid)
        return self._summary(sid)

    def get(self, sid: str) -> dict[str, Any] | None:
        self._fresh()
        return self._sessions.get(sid)

    def detail(self, sid: str) -> dict[str, Any] | None:
        self._fresh()
        s = self._sessions.get(sid)
        if not s:
            return None
        return {
            "id": sid,
            "title": s["title"],
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
            "transcript": s.get("transcript", []),
            "condenses": s.get("condenses", []),
        }

    def messages(self, sid: str) -> list[Any]:
        self._fresh()
        s = self._sessions.get(sid)
        return list(s.get("messages", [])) if s else []

    def engine_session(self, sid: str) -> dict[str, Any] | None:
        """The subscription CLI session this chat continues: {provider, model, id}."""
        self._fresh()
        s = self._sessions.get(sid)
        return s.get("engine_session") if s else None

    def set_engine_session(self, sid: str, value: dict[str, Any] | None) -> None:
        with self._edit():
            s = self._sessions.get(sid)
            if s is not None:
                s["engine_session"] = value

    def set_condensed(self, sid: str, event: dict[str, Any], messages: list[Any]) -> None:
        """The chat was condensed (agent/condense.py): keep the summary for the next
        condense and the event for the thread, shrink the agent history, and drop
        the CLI session so the next message starts a fresh one from the summary."""
        with self._edit():
            s = self._sessions.get(sid)
            if s is None:
                return
            s["condensed"] = {"summary": event["summary"], "through": event["through"], "at": event["at"]}
            s.setdefault("condenses", []).append(event)
            s["messages"] = messages
            s["engine_session"] = None

    def ensure(self, sid: str | None) -> str:
        """Return `sid` if it names a live session, else make a new one."""
        self._fresh()
        if sid and sid in self._sessions:
            return sid
        return self.new()["id"]

    def record(self, sid: str, turn: dict[str, Any], messages: list[Any]) -> None:
        """Append a display turn, persist the agent messages, bump recency."""
        with self._edit():
            s = self._sessions.get(sid)
            if not s:
                return  # deleted while the reply streamed
            s["transcript"].append(turn)
            s["messages"] = messages
            s["updated_at"] = _now()
            if s["title"] in ("", "New chat"):
                # An image-only turn is titled by its first attachment.
                label = turn.get("q") or next(
                    (i.get("name", "") for i in turn.get("images", [])), ""
                )
                if label:
                    s["title"] = _title_from(label)
            if sid in self._order:
                self._order.remove(sid)
            self._order.insert(0, sid)

    def delete(self, sid: str) -> None:
        from pcb_assistant.agent.chat_images import IMAGES_REL, delete_images

        with self._edit():
            s = self._sessions.pop(sid, None)
            if sid in self._order:
                self._order.remove(sid)
        if s:  # its attachments go with it
            ids = [i.get("id", "") for t in s.get("transcript", []) for i in t.get("images", [])]
            delete_images(self.path.parent / IMAGES_REL.name, ids)
