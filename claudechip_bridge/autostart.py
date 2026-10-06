#!/usr/bin/python3
"""Starts the bridge when you log in to the Mac (LaunchAgent set up by
/claudechip:setup), so the device connects without opening Claude Code first.

A copy of this file lives in ~/.config/claudechip/: the plugin folder changes
with every update, so it runs the plugin version recorded by the last
SessionStart (plugin_root). If a bridge is already running the new one exits.
"""

import os
from pathlib import Path

DATA_DIR = Path.home() / ".config" / "claudechip"


def main() -> None:
    try:
        root = (DATA_DIR / "plugin_root").read_text().strip()
    except OSError:
        return  # no session has run with the plugin yet
    config = DATA_DIR / "plugin-config.toml"
    if not os.path.isdir(os.path.join(root, "claudechip_bridge")) or not config.exists():
        return
    os.chdir(root)
    os.environ["PYTHONPATH"] = root
    os.execv("/usr/bin/python3", ["/usr/bin/python3", "-m", "claudechip_bridge", "--config", str(config)])


if __name__ == "__main__":
    main()
