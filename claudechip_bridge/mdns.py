"""Announce the bridge on the LAN as _claudechip._tcp."""

from __future__ import annotations

import asyncio
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from . import __version__
from .config import Config

SERVICE_TYPE = "_claudechip._tcp.local."
REFRESH_S = 20  # how often to check whether the Mac's address changed


def lan_ip() -> str | None:
    """IP of the interface used for the default route (no packet is sent),
    or None while the Mac has no network (e.g. just woke up or moved)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


class Advertiser:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Unique per machine: several Macs may share an account
        host = socket.gethostname().split(".")[0].lower()
        self.name = f"{cfg.account.lower()}-{host}.{SERVICE_TYPE}"
        self._zc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None
        self._ip: str | None = None

    def _service(self, ip: str) -> ServiceInfo:
        return ServiceInfo(
            SERVICE_TYPE,
            self.name,
            addresses=[socket.inet_aton(ip)],
            port=self.cfg.port,
            properties={"account": self.cfg.account, "version": __version__},
            server=f"{socket.gethostname().split('.')[0]}.local.",
        )

    async def start(self) -> None:
        # All interfaces: restricting zeroconf to the LAN address stopped the
        # device from finding the bridge.
        self._zc = AsyncZeroconf()
        await self.refresh()

    async def refresh(self) -> None:
        """Follow the Mac's address: announce once it has one and update the
        announcement when it changes (wake from sleep, another network).
        Never announces 127.0.0.1."""
        ip = lan_ip()
        if ip is None or ip == self._ip:
            return
        info = self._service(ip)
        if self._info is None:
            await self._zc.async_register_service(info, allow_name_change=True)
            self.name = info.name
        else:
            await self._zc.async_update_service(info)
        self._info, self._ip = info, ip

    async def watch(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_S)
            try:
                await self.refresh()
            except Exception:  # keep trying on the next round
                pass

    async def stop(self) -> None:
        if self._zc:
            if self._info:
                await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
