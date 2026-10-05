"""Minimal WebSocket server side (RFC 6455) on asyncio streams.

Text messages, ping/pong heartbeat and close. Standard library only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import time
from typing import Optional

from .http import Request

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def is_upgrade(request: Request) -> bool:
    return (request.headers.get("upgrade", "").lower() == "websocket"
            and "sec-websocket-key" in request.headers)


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, remote: str) -> None:
        self.reader = reader
        self.writer = writer
        self.remote = remote
        self.closed = False
        self._lock = asyncio.Lock()
        self._last_pong = time.monotonic()

    @classmethod
    async def accept(cls, request: Request) -> "WebSocket":
        key = request.headers["sec-websocket-key"]
        accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        request.writer.write(("HTTP/1.1 101 Switching Protocols\r\n"
                              "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await request.writer.drain()
        return cls(request.reader, request.writer, request.remote)

    # ------------------------------------------------------------------ send

    async def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        if self.closed and opcode != OP_CLOSE:
            raise ConnectionError("websocket closed")
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 1 << 16:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        async with self._lock:  # frames from different tasks must not interleave
            self.writer.write(head + payload)
            await self.writer.drain()

    async def send_str(self, text: str) -> None:
        await self._send_frame(OP_TEXT, text.encode())

    async def send_json(self, data) -> None:
        await self.send_str(json.dumps(data, ensure_ascii=False))

    async def close(self, code: int = 1000, reason: bytes = b"") -> None:
        if self.closed:
            return
        try:
            await self._send_frame(OP_CLOSE, struct.pack("!H", code) + reason[:120])
        except ConnectionError:
            pass
        self.closed = True
        self.writer.close()

    # --------------------------------------------------------------- receive

    async def recv(self) -> Optional[str]:
        """Next text message, or None once the connection is closed."""
        parts = []
        while not self.closed:
            try:
                b1, b2 = await self.reader.readexactly(2)
                opcode, fin = b1 & 0x0F, b1 & 0x80
                n = b2 & 0x7F
                if n == 126:
                    (n,) = struct.unpack("!H", await self.reader.readexactly(2))
                elif n == 127:
                    (n,) = struct.unpack("!Q", await self.reader.readexactly(8))
                if n > MAX_MESSAGE:
                    await self.close(1009)
                    return None
                mask = await self.reader.readexactly(4) if b2 & 0x80 else b""
                data = await self.reader.readexactly(n)
            except (asyncio.IncompleteReadError, ConnectionError):
                self.closed = True
                return None
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

            if opcode == OP_PING:
                await self._send_frame(OP_PONG, data)
            elif opcode == OP_PONG:
                self._last_pong = time.monotonic()
            elif opcode == OP_CLOSE:
                await self.close()
                return None
            elif opcode in (OP_TEXT, OP_CONT):
                parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")
            # binary frames are ignored
        return None

    async def heartbeat(self, every: float = 20, timeout: float = 30) -> None:
        """Ping regularly; close when the peer stops answering (asleep, gone)."""
        while not self.closed:
            await asyncio.sleep(every)
            if time.monotonic() - self._last_pong > every + timeout:
                await self.close(1001)
                return
            try:
                await self._send_frame(OP_PING)
            except ConnectionError:
                return
