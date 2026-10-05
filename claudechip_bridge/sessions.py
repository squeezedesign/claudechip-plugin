"""Claude Code sessions seen on this Mac, keyed by session id."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# Status values understood by the device. ASK: Claude waits for the user in
# the console (plan approval, a question) and the device can only call attention.
WORK, PERM, DONE, ERR, ASK = "work", "perm", "done", "err", "ask"

NAME_MAX = 23  # firmware Session::name is 24 bytes


def session_name(cwd: str) -> str:
    """Tab name: the project folder, uppercase."""
    name = os.path.basename(os.path.normpath(cwd)) or cwd
    name = name.upper()
    # Keep it within the firmware buffer, counting UTF-8 bytes
    while len(name.encode()) > NAME_MAX:
        name = name[:-1]
    return name


@dataclass
class Session:
    id: str
    cwd: str
    name: str
    status: str = DONE
    ctx: int = 0
    text: str = ""
    updated: float = field(default_factory=time.time)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "ctx": self.ctx,
            "text": self.text,
        }


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
        return sorted(self._sessions.values(), key=lambda s: s.name)
