#!/usr/bin/env python3
"""PermissionRequest hook: forward the event to the bridge and wait for the answer.

The HTTP connection lives in this very process, so when Claude Code kills
the hook (the user answered in the terminal) the connection closes and the
bridge withdraws the request from the device. Any error, including a bridge
that is not running, ends silently with no decision so Claude Code just
asks in the terminal as usual.

Standard library only, Python 3.9 compatible (runs with the system python3).

Usage: forward.py [url]   (default: the plugin's port, CLAUDE_PLUGIN_OPTION_PORT)
"""

import os
import sys
import urllib.request


def main() -> None:
    port = os.environ.get("CLAUDE_PLUGIN_OPTION_PORT") or "8765"
    url = sys.argv[1] if len(sys.argv) > 1 else f"http://127.0.0.1:{port}/hook/PermissionRequest"
    data = sys.stdin.buffer.read()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:  # no timeout: wait for an answer
            sys.stdout.buffer.write(resp.read())
    except Exception:
        pass


if __name__ == "__main__":
    main()
