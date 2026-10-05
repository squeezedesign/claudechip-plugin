"""Announce the bridge on the LAN as _claudechip._tcp with macOS's dns-sd.

mDNSResponder does the actual work: it follows address changes, network
switches and sleep/wake by itself, so the bridge never announces a stale or
loopback address. Standard library only.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Optional

from . import __version__
from .config import Config

SERVICE_TYPE = "_claudechip._tcp"
RESTART_S = 5  # if dns-sd exits, start it again after this long


class Advertiser:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Unique per machine: several Macs may share an account
        host = socket.gethostname().split(".")[0].lower()
        self.name = f"{cfg.account.lower()}-{host}"
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    async def _spawn(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "dns-sd", "-R", self.name, SERVICE_TYPE, "local", str(self.cfg.port),
            f"account={self.cfg.account}", f"version={__version__}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )

    async def start(self) -> None:
        await self._spawn()
        self._task = asyncio.ensure_future(self._keep_alive())

    async def _keep_alive(self) -> None:
        while not self._stopping:
            await self._proc.wait()
            if self._stopping:
                return
            await asyncio.sleep(RESTART_S)
            await self._spawn()

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
