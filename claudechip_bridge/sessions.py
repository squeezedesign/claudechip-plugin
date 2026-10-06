"""Claude Code sessions seen on this Mac, keyed by session id."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# Status values understood by the device. ASK: Claude waits for the user in
# the console (plan approval, a question) and the device can only call attention.
# QUESTION: Claude ended with a yes/no question the device can answer.
WORK, PERM, DONE, ERR, ASK, QUESTION = "work", "perm", "done", "err", "ask", "question"

NAME_MAX = 23     # firmware Session::name is 24 bytes
HISTORY_MAX = 20  # summaries kept per session for the device's history view


def _fit(name: str, limit: int = NAME_MAX) -> str:
    """Keep a name within the firmware buffer, counting UTF-8 bytes."""
    while len(name.encode()) > limit:
        name = name[:-1]
    return name


def session_name(cwd: str) -> str:
    """Tab name: the project folder, uppercase."""
    name = os.path.basename(os.path.normpath(cwd)) or cwd
    return _fit(name.upper())


@dataclass
class Session:
    id: str
    cwd: str
    name: str
    status: str = DONE
    ctx: int = 0
    text: str = ""
    title: str = ""          # Claude Code's title for the conversation
    transcript: str = ""     # path of its transcript, from the hooks
    started: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    # Latest summaries, oldest first: {"t": epoch, "status", "text"}
    history: list = field(default_factory=list)

    def to_json(self, name: str | None = None) -> dict:
        return {
            "id": self.id,
            "name": name or self.name,
            "title": self.title,
            "status": self.status,
            "ctx": self.ctx,
            "text": self.text,
        }

    def add_history(self, status: str, text: str) -> None:
        if self.history and self.history[-1]["text"] == text:
            return
        self.history.append({"t": time.time(), "status": status, "text": text})
        del self.history[:-HISTORY_MAX]


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, folder: str) -> Session:
        """folder should be the project root; the name is fixed when the
        session is first seen so it does not change when Claude cd's around."""
        s = self._sessions.get(session_id)
        if s is None:
            s = Session(id=session_id, cwd=folder, name=session_name(folder))
            self._sessions[session_id] = s
        return s

    def set_project(self, session_id: str, project: str) -> None:
        """The real project root became known: rename the tab once."""
        s = self._sessions.get(session_id)
        if s and project and s.cwd != project:
            s.cwd, s.name = project, session_name(project)

    def update(self, s: Session, *, status: str | None = None, text: str | None = None) -> None:
        if status is not None:
            s.status = status
        if text is not None:
            s.text = text
        s.updated = time.time()

    def remove(self, session_id: str) -> Session | None:
        return self._sessions.pop(session_id, None)

    def all(self) -> list[Session]:
        return sorted(self._sessions.values(), key=lambda s: (s.name, s.started))

    def to_json(self) -> list[dict]:
        """Sessions for the device. Two sessions of the same project get a
        number ("CLAUDE-CHIP", "CLAUDE-CHIP 2") in the order they started."""
        seen: dict[str, int] = {}
        result = []
        for s in self.all():
            n = seen[s.name] = seen.get(s.name, 0) + 1
            name = s.name if n == 1 else _fit(s.name, NAME_MAX - len(f" {n}")) + f" {n}"
            result.append(s.to_json(name))
        return result
