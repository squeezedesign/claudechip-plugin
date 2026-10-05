"""Command line entry point.

  python3 -m claudechip_bridge         run the bridge (or: uv run claudechip-bridge)
  uv run claudechip-bridge install     add hooks + status line to ~/.claude
  uv run claudechip-bridge uninstall   remove them
  uv run claudechip-bridge usage-check check that plan usage can be read directly
  uv run claudechip-bridge pair 123456 pair the device showing that code
  uv run claudechip-bridge devices     list paired devices
  uv run claudechip-bridge revoke ID   forget a device (id or prefix)
  ... status                           is the bridge running, and how
  ... setup-plugin / remove-plugin-setup   status line for the Claude Code plugin
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from . import config, install
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="claudechip-bridge")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "install", "uninstall", "usage-check", "pair", "devices", "revoke",
                                 "status", "setup-plugin", "remove-plugin-setup"])
    parser.add_argument("arg", nargs="?", help="pair: the code; revoke: the device id")
    parser.add_argument("extra", nargs="?", help="devices revoke <id>: the device id")
    parser.add_argument("--config", type=Path, help="config file (default: bridge/config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--raw", action="store_true", help="usage-check: print the full answer")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "usage-check":
        from . import oauth_usage
        oauth_usage.check(raw=args.raw)
        return

    # zeroconf logs a harmless "No route to host" traceback for interfaces
    # without a route (VPN tunnels, inactive adapters); keep the output clean
    logging.getLogger("zeroconf").setLevel(logging.CRITICAL)
    config_path = args.config
    if (args.command == "status" and config_path is None and not config.DEFAULT_CONFIG.exists()
            and not config.plugin_config_path().exists()):
        # Plugin installed but no session has started since: nothing is wrong
        print("the bridge has not started yet: it starts with the next Claude Code session")
        return
    cfg = config.load(config_path)

    if args.command in ("pair", "revoke"):
        _local_command(cfg, args.command, args.arg)
        return
    if args.command == "devices" and args.arg == "revoke":
        _local_command(cfg, "revoke", args.extra)
        return
    if args.command == "devices":
        from .identity import Devices
        devices = Devices().all()
        if not devices:
            print("no paired devices")
        for device_id, info in devices.items():
            print(f"{device_id}   paired {info.get('paired_at', '?')}")
        return

    if args.command == "status":
        _status(cfg)
        return
    if args.command == "setup-plugin":
        if args.arg == "remove":
            install.remove_plugin_setup(cfg)
        else:
            install.setup_plugin(cfg)
        return
    if args.command == "remove-plugin-setup":
        install.remove_plugin_setup(cfg)
        return

    if args.command == "install":
        install.install(cfg)
    elif args.command == "uninstall":
        install.uninstall(cfg)
    else:
        try:
            used = config_path or (config.DEFAULT_CONFIG if config.DEFAULT_CONFIG.exists()
                                   else config.plugin_config_path())
            fingerprint = hashlib.sha1(Path(used).read_bytes()).hexdigest()[:12]
            asyncio.run(serve(cfg, fingerprint))
        except KeyboardInterrupt:
            pass



def _local_command(cfg, command: str, arg: str | None) -> None:
    """pair / revoke talk to the running bridge on this Mac."""
    if not arg:
        raise SystemExit(f"usage: claudechip-bridge {command} <{'code' if command == 'pair' else 'device id'}>")
    key = "code" if command == "pair" else "device"
    req = urllib.request.Request(
        f"http://127.0.0.1:{cfg.port}/{command}",
        data=json.dumps({key: arg}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            answer = json.loads(resp.read())
    except urllib.error.URLError:
        raise SystemExit("the bridge is not running: start it with 'python3 -m claudechip_bridge'")
    if not answer.get("ok"):
        raise SystemExit(f"{command} failed: {answer.get('error')}")
    print(f"{'paired' if command == 'pair' else 'revoked'}: {answer['device']}")



def _status(cfg) -> None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.port}/health", timeout=3) as resp:
            h = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        print(f"bridge not running (port {cfg.port})")
        return
    devices = "connected" if h.get("devices_connected") else "not connected"
    print(f"bridge {h['account']} running on port {h['port']} (v{h['version']})")
    print(f"device: {devices}, {h.get('devices_paired', 0)} paired")
    print(f"sessions: {h.get('sessions', 0)}, summaries: {h.get('summary')}, usage: {h.get('usage_source')}")

if __name__ == "__main__":
    main()
