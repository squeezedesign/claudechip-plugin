#!/usr/bin/env python3
"""Stop hook: forward the event to the bridge and, when Claude ended with a
yes/no question ([DECK?] line), wait for the answer from the device.

It runs in the background (async + asyncRewake in hooks.json), so it never
blocks the terminal. When the device answers, the bridge replies with the
answer text: this hook prints it on stderr and exits with code 2, which
wakes Claude up with that text even if the session is idle. Answering in
the terminal (or closing the session) makes the bridge release it with no
answer. Any error, including a bridge that is not running, ends silently.

Standard library only, Python 3.9 compatible (runs with the system python3).
"""

import json
import os
import sys
import urllib.request


def main() -> int:
    port = os.environ.get("CLAUDE_PLUGIN_OPTION_PORT") or "8765"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/hook/Stop", data=sys.stdin.buffer.read(),
        headers={"Content-Type": "application/json",
                 "X-Claude-Pid": str(os.getppid()),
                 "X-Claude-Project": os.environ.get("CLAUDE_PROJECT_DIR", ""),
                 "X-Claude-Wait": "1"})  # this hook can carry an answer back
    try:
        with urllib.request.urlopen(req) as resp:  # no timeout: it runs in the background
            body = resp.read()
        answer = json.loads(body).get("answer") if body else None
    except Exception:
        return 0
    if not answer:
        return 0
    sys.stderr.write(answer)
    return 2  # asyncRewake: Claude reads stderr as a system reminder and goes on


if __name__ == "__main__":
    sys.exit(main())
