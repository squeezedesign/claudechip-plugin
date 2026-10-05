"""Bridge identity and paired devices (SPEC §7.3, pairing).

Each bridge has a stable random id. Each paired device gets its own random
token, stored here and in the device's NVS. The token travels only once, in
the pairing reply; afterwards both sides prove they know it with HMAC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

# CLAUDECHIP_DATA_DIR keeps tests away from the real paired devices
DATA_DIR = Path(os.environ.get("CLAUDECHIP_DATA_DIR") or Path.home() / ".config" / "claudechip")
BRIDGE_ID_FILE = DATA_DIR / "bridge_id"
DEVICES_FILE = DATA_DIR / "devices.json"


def sign(token_hex: str, text: str) -> str:
    """HMAC-SHA256 of text with the device token, as lowercase hex."""
    return hmac.new(bytes.fromhex(token_hex), text.encode(), hashlib.sha256).hexdigest()


def bridge_id() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if BRIDGE_ID_FILE.exists():
        value = BRIDGE_ID_FILE.read_text().strip()
        if value:
            return value
    value = secrets.token_hex(6)
    BRIDGE_ID_FILE.write_text(value + "\n")
    return value


class Devices:
    """Paired devices: {device_id: {"token", "paired_at"}} in devices.json."""

    def __init__(self) -> None:
        self._devices: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            self._devices = json.loads(DEVICES_FILE.read_text())
        except (OSError, ValueError):
            self._devices = {}

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DEVICES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._devices, indent=2))
        tmp.chmod(0o600)  # tokens: owner only
        tmp.replace(DEVICES_FILE)

    def token(self, device_id: str) -> str | None:
        entry = self._devices.get(device_id)
        return entry["token"] if entry else None

    def pair(self, device_id: str) -> str:
        """New token for the device (replaces an older one)."""
        token = secrets.token_hex(32)
        self._devices[device_id] = {"token": token, "paired_at": time.strftime("%Y-%m-%d %H:%M")}
        self._save()
        return token

    def revoke(self, device_id: str) -> str | None:
        """Remove a device by id or unique prefix; returns the full id."""
        matches = [d for d in self._devices if d.startswith(device_id.lower())]
        if len(matches) != 1:
            return None
        del self._devices[matches[0]]
        self._save()
        return matches[0]

    def all(self) -> dict[str, dict]:
        return dict(self._devices)
