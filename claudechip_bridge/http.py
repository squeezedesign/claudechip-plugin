"""Minimal HTTP/1.1 server on asyncio streams (standard library only).

Enough for the bridge: one request per connection, JSON bodies, and handing
the connection over for a WebSocket upgrade.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

MAX_BODY = 1 << 20  # hook payloads are small; refuse anything absurd

REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
           413: "Payload Too Large", 500: "Internal Server Error"}


@dataclass
class Request:
    method: str
    path: str
    headers: dict
    body: bytes
    remote: str
    reader: asyncio.StreamReader = field(repr=False)
    writer: asyncio.StreamWriter = field(repr=False)

    def json(self) -> dict:
        data = json.loads(self.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    async def wait_disconnect(self) -> None:
        """Returns when the client closes the connection (nothing else is
        expected from it after the body)."""
        while await self.reader.read(1024):
            pass


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json"

    @staticmethod
    def json(data, status: int = 200) -> "Response":
        return Response(status, json.dumps(data, ensure_ascii=False).encode())


Handler = Callable[[Request], Awaitable[Optional[Response]]]


async def start(handler: Handler, host: str, port: int) -> asyncio.AbstractServer:
    """Serve until the returned server is closed. A handler that takes over the
    connection (WebSocket) returns None and owns the writer from then on."""

    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        remote = peer[0] if peer else "?"
        response: Optional[Response] = None
        try:
            request = await _read_request(reader, writer, remote)
            if request is None:
                writer.close()
                return
            response = await handler(request)
        except _HttpError as e:
            response = Response(e.status)
        except (ConnectionError, asyncio.IncompleteReadError):
            writer.close()
            return
        except Exception:  # a bug in a handler must not leave the client hanging
            logging.getLogger("bridge").exception("error handling a request")
            response = Response(500)
        if response is None:
            return  # upgraded to a WebSocket and already closed by its owner
        try:
            head = (f"HTTP/1.1 {response.status} {REASONS.get(response.status, '')}\r\n"
                    f"Content-Type: {response.content_type}\r\n"
                    f"Content-Length: {len(response.body)}\r\n"
                    "Connection: close\r\n\r\n")
            writer.write(head.encode() + response.body)
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    return await asyncio.start_server(on_client, host, port)


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


async def _read_request(reader, writer, remote: str) -> Optional[Request]:
    line = await reader.readline()
    if not line:
        return None
    try:
        method, target, _version = line.decode("latin-1").split(" ", 2)
    except ValueError:
        raise _HttpError(400) from None
    headers = {}
    while True:
        raw = await reader.readline()
        if raw in (b"\r\n", b"\n", b""):
            break
        name, _, value = raw.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    if length > MAX_BODY:
        raise _HttpError(413)
    body = await reader.readexactly(length) if length else b""
    return Request(method.upper(), target.split("?", 1)[0], headers, body, remote, reader, writer)
