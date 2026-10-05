"""Command line entry point.

  uv run claudechip-bridge             run the bridge
  uv run claudechip-bridge install     add hooks + status line to ~/.claude
  uv run claudechip-bridge uninstall   remove them
  uv run claudechip-bridge usage-check check that plan usage can be read directly
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from . import config, install
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="claudechip-bridge")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "install", "uninstall", "usage-check"])
    parser.add_argument("--config", type=Path, help="config file (default: bridge/config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "usage-check":
        from . import oauth_usage
        oauth_usage.check()
        return

    # zeroconf logs a harmless "No route to host" traceback for interfaces
    # without a route (VPN tunnels, inactive adapters); keep the output clean
    logging.getLogger("zeroconf").setLevel(logging.CRITICAL)
    cfg = config.load(args.config)

    if args.command == "install":
        install.install(cfg)
    elif args.command == "uninstall":
        install.uninstall(cfg)
    else:
        try:
            asyncio.run(serve(cfg))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
