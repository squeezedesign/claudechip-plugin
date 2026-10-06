"""Is the user at this Mac? Seconds since the last key press or mouse move.

macOS keeps this in the HID system (the same figure screen savers use); it
needs no permission and keeps growing while the screen is locked.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

_IDLE_RE = re.compile(r'"HIDIdleTime" = (\d+)')


def idle_seconds() -> Optional[float]:
    """Seconds without keyboard or mouse input, or None if unknown."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4", "-r", "-k", "HIDIdleTime"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = _IDLE_RE.search(out)
    return int(match.group(1)) / 1e9 if match else None
