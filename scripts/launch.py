#!/usr/bin/env python3
"""SessionStart hook of the Claude Chip plugin.

1. Writes the bridge config from the plugin options (CLAUDE_PLUGIN_OPTION_*).
2. Makes sure one bridge is running on this Mac: starts it in the background
   if needed, and restarts it when the options or the plugin version changed.
3. Forwards the SessionStart event to the bridge.
4. Adds the [DECK] rule to the session context (summary = "deck_line").

Never fails the session: any problem only means the device sees nothing.
Standard library only, Python 3.9+.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.realpath(os.environ.get("CLAUDE_PLUGIN_ROOT")
                        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from claudechip_bridge.config import plugin_config_path  # noqa: E402
from claudechip_bridge.install import DECK_RULE  # noqa: E402

LOG_MAX_BYTES = 1 << 20
START_WAIT_S = 4


def option(key: str, default: str) -> str:
    value = os.environ.get("CLAUDE_PLUGIN_OPTION_" + key.upper(), "").strip()
    return value or default


def desired_config() -> dict:
    host = socket.gethostname().split(".")[0].replace("-", " ")
    return {
        "account": option("account", "CLAUDE").upper(),
        "mac": option("mac_name", host).upper(),
        "color": option("color", "#D7DF23"),
        "port": int(option("port", "8765")),
        "summary": option("summary", "deck_line"),
        "usage_source": option("usage_source", "auto"),
        "open_in": option("open_in", "auto"),
    }


def write_config(cfg: dict) -> str:
    """Write the TOML the bridge reads; returns its fingerprint."""
    lines = ["# Written by the Claude Chip plugin from its options. Do not edit:",
             "# change the plugin options in Claude Code (/plugin) instead."]
    for key, value in cfg.items():
        lines.append(f"{key} = {value}" if isinstance(value, int) else f'{key} = "{value}"')
    text = "\n".join(lines) + "\n"
    path = plugin_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def health(port: int):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def post(port: int, path: str, body: bytes, headers=None, timeout: float = 2) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        urllib.request.urlopen(req, timeout=timeout).close()
    except (urllib.error.URLError, OSError):
        pass


def start_bridge(port: int) -> None:
    log_path = plugin_config_path().parent / "bridge.log"
    if log_path.exists() and log_path.stat().st_size > LOG_MAX_BYTES:
        log_path.unlink()
    log = open(log_path, "ab")
    subprocess.Popen(
        ["/usr/bin/python3", "-m", "claudechip_bridge", "--config", str(plugin_config_path())],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": ROOT},
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,  # keeps running when this session ends
    )
    deadline = time.time() + START_WAIT_S
    while time.time() < deadline and not health(port):
        time.sleep(0.2)


def main() -> None:
    event = sys.stdin.buffer.read()
    cfg = desired_config()
    fingerprint = write_config(cfg)
    port = cfg["port"]

    running = health(port)
    if running and (running.get("config_id") != fingerprint or running.get("root") != ROOT):
        # Options changed or the plugin was updated: restart with the new ones
        post(port, "/shutdown", b"{}")
        for _ in range(20):
            if not health(port):
                break
            time.sleep(0.1)
        running = None
    if not running:
        start_bridge(port)
    # Where the login LaunchAgent (/claudechip:setup) finds the current version
    root_file = plugin_config_path().parent / "plugin_root"
    if not root_file.exists() or root_file.read_text().strip() != ROOT:
        root_file.write_text(ROOT + "\n")

    post(port, "/hook/SessionStart", event, {
        "X-Claude-Pid": str(os.getppid()),
        "X-Claude-Project": os.environ.get("CLAUDE_PROJECT_DIR", ""),
    })

    if cfg["summary"] == "deck_line":
        rule = DECK_RULE.split("\n", 1)[1].rsplit("\n", 2)[0]  # without the HTML markers
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": rule}}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never break the session
        pass
