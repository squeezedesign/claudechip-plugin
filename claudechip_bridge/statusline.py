#!/usr/bin/env python3
"""Status line wrapper installed by the Claude Chip bridge.

Claude Code runs this with the session JSON on stdin. It forwards the JSON to
the bridge (context window and plan usage) and then runs the status line
command that was configured before, so the visible status line is unchanged.

Standard library only and Python 3.9 compatible: it runs with the system
python3 on every status line refresh, so it must start fast.

Usage: statusline.py <bridge port>
"""

import json
import os
import subprocess
import sys
import urllib.request

PREVIOUS = os.path.expanduser("~/.config/claudechip/statusline_previous.json")


def forward(port: str, data: bytes) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/statusline",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=0.3).close()
    except Exception:
        pass  # bridge not running: the status line must still work


def previous_command():
    try:
        with open(PREVIOUS) as f:
            return (json.load(f) or {}).get("command")
    except (OSError, ValueError):
        return None


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "8765"
    data = sys.stdin.buffer.read()
    forward(port, data)

    command = previous_command()
    if command:
        result = subprocess.run(command, shell=True, input=data, capture_output=True)
        sys.stdout.buffer.write(result.stdout)


if __name__ == "__main__":
    main()
