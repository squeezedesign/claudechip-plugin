#!/usr/bin/env python3
"""PreToolUse hook for AskUserQuestion: lets the device answer Claude's
multiple-choice questions when the user is away from the Mac.

The bridge decides: if the user touched the Mac recently, or no device is
connected, it answers at once with nothing and the question shows in the
terminal as usual. Otherwise the device shows it, and the chosen options come
back as the tool's answers (the documented way to answer AskUserQuestion).
Any error, including a bridge that is not running, ends silently.

Standard library only, Python 3.9 compatible (runs with the system python3).
"""

import os
import sys
import urllib.request


def main() -> None:
    port = os.environ.get("CLAUDE_PLUGIN_OPTION_PORT") or "8765"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/hook/Ask", data=sys.stdin.buffer.read(),
        headers={"Content-Type": "application/json",
                 "X-Claude-Pid": str(os.getppid()),
                 "X-Claude-Project": os.environ.get("CLAUDE_PROJECT_DIR", "")})
    try:
        with urllib.request.urlopen(req) as resp:  # no timeout: the bridge decides
            sys.stdout.buffer.write(resp.read())
    except Exception:
        pass


if __name__ == "__main__":
    main()
