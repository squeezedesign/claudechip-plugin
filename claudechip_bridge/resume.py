"""Open a recent session again on this Mac: `claude --resume <id>` in its folder.

Where it opens comes from the "open_in" option: iTerm2 or Terminal.app via
AppleScript, or PhpStorm (opens the project and copies the command to the
clipboard, since its terminal cannot be scripted). The first time, macOS asks
whether the bridge may control iTerm / Terminal.
"""

from __future__ import annotations

import os
import shlex
import subprocess

from .transcripts import is_session_id

OSASCRIPT_TIMEOUT_S = 60  # the macOS Automation prompt blocks until answered
ITERM_PATHS = ("/Applications/iTerm.app", os.path.expanduser("~/Applications/iTerm.app"))

# The command goes in as an argument, never pasted into the script: no quoting issues
_ITERM = """on run argv
  tell application "iTerm"
    activate
    set w to (create window with default profile)
    tell current session of w to write text (item 1 of argv)
  end tell
end run"""

_TERMINAL = """on run argv
  tell application "Terminal"
    activate
    do script (item 1 of argv)
  end tell
end run"""


class ResumeError(Exception):
    pass


def resume_command(cwd: str, session_id: str) -> str:
    return f"cd {shlex.quote(cwd)} && claude --resume {session_id}"


def _osascript(script: str, command: str) -> None:
    try:
        result = subprocess.run(["osascript", "-e", script, command], capture_output=True,
                                text=True, timeout=OSASCRIPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResumeError("macOS did not answer (permission prompt?)") from None
    if result.returncode != 0:
        error = result.stderr.strip()
        if "-1743" in error or "Not authorized" in error:
            raise ResumeError("not allowed: System Settings > Privacy > Automation")
        raise ResumeError(error or "osascript failed")


def _phpstorm(cwd: str, command: str) -> None:
    subprocess.run(["pbcopy"], input=command, text=True, check=False)
    result = subprocess.run(["open", "-a", "PhpStorm", cwd], capture_output=True, text=True)
    if result.returncode != 0:
        raise ResumeError("PhpStorm not found")


def target(open_in: str) -> str:
    if open_in == "auto":
        return "iterm" if any(os.path.isdir(p) for p in ITERM_PATHS) else "terminal"
    return open_in


def resume(cwd: str, session_id: str, open_in: str) -> str:
    """Open the session; returns where it opened. Blocking: run it in a thread."""
    if not is_session_id(session_id) or not os.path.isdir(cwd):
        raise ResumeError("unknown session")
    command = resume_command(cwd, session_id)
    where = target(open_in)
    if where == "iterm":
        _osascript(_ITERM, command)
    elif where == "terminal":
        _osascript(_TERMINAL, command)
    elif where == "phpstorm":
        _phpstorm(cwd, command)
    else:
        raise ResumeError(f"unknown open_in {open_in!r}")
    return where
