"""Announce the bridge on the LAN as _claudechip._tcp."""

from __future__ import annotations

import socket

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from . import __version__
from .config import Config

SERVICE_TYPE = "_claudechip._tcp.local."


def lan_ip() -> str:
    """IP of the interface used for the default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class Advertiser:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Unique per machine: several Macs may share an account
        host = socket.gethostname().split(".")[0].lower()
        self.name = f"{cfg.account.lower()}-{host}.{SERVICE_TYPE}"
        self._zc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None

    async def start(self) -> None:
        ip = lan_ip()
        self._info = ServiceInfo(
            SERVICE_TYPE,
            self.name,
            addresses=[socket.inet_aton(ip)],
            port=self.cfg.port,
            properties={"account": self.cfg.account, "version": __version__},
            server=f"{socket.gethostname().split('.')[0]}.local.",
        )
        # Only the LAN interface: announcing on every interface (VPN tunnels,
        # inactive adapters) logs "No route to host" errors at startup
        self._zc = AsyncZeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
        await self._zc.async_register_service(self._info, allow_name_change=True)
        self.name = self._info.name

    async def stop(self) -> None:
        if self._zc and self._info:
            await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
