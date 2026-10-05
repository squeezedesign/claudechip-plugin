"""Permission requests waiting for a decision from the device.

The PermissionRequest hook stays open until the device answers or the
request is answered in the terminal. Claude Code does not cancel the hook on
a terminal answer, so the bridge infers it from the next event of the same
session and releases the hook with no decision. There is no timeout:
whichever answer comes first wins.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field


@dataclass
class Pending:
    request_id: str
    session_id: str
    tool_use_id: str
    text: str
    future: asyncio.Future = field(repr=False)

    def to_json(self) -> dict:
        return {
            "type": "permission",
            "request_id": self.request_id,
            "session_id": self.session_id,
            "text": self.text,
        }


class Permissions:
    def __init__(self) -> None:
        self._pending: dict[str, Pending] = {}
        self._ids = itertools.count(1)

    def create(self, session_id: str, tool_use_id: str, text: str) -> Pending:
        request_id = f"r{next(self._ids)}"
        p = Pending(request_id, session_id, tool_use_id, text,
                    asyncio.get_running_loop().create_future())
        self._pending[request_id] = p
        return p

    def resolve(self, request_id: str, allow: bool | None) -> bool:
        """Answer the request: True/False from the device, None to release the
        hook without a decision. False if the request is gone already."""
        p = self._pending.pop(request_id, None)
        if p is None or p.future.done():
            return False
        p.future.set_result(allow)
        return True

    def discard(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    def for_session(self, session_id: str) -> list[Pending]:
        return [p for p in self._pending.values() if p.session_id == session_id]

    def all(self) -> list[Pending]:
        return list(self._pending.values())
