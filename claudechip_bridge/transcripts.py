"""What the bridge reads from Claude Code's transcripts (~/.claude/projects).

- The title Claude Code gives each conversation (the one in its history list),
  to tell apart two sessions of the same project.
- The recent sessions of this Mac, for the device's "↺ RECIENTES" list.

Transcripts are JSON lines and can be large: only the head (where the
working folder is) and the tail (latest title and answer) are read.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from .summary import clip, summarize

PROJECTS_DIR = Path.home() / ".claude" / "projects"
HEAD_BYTES = 64 * 1024
TAIL_BYTES = 256 * 1024
TITLE_MAX = 60
RECENT_LIMIT = 8
RECENT_DAYS = 7
# Sessions run in temporary folders (tests, scratchpads) are not worth resuming
TEMP_PREFIXES = ("/private/tmp/", "/tmp/", "/private/var/folders/", "/var/folders/")
SESSION_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _read(path, start: int, size: int) -> list[dict]:
    """JSON entries in a byte range; lines cut at the edges are skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(start)
            chunk = f.read(size).decode("utf-8", "replace")
    except OSError:
        return []
    entries = []
    for line in chunk.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _tail(path) -> list[dict]:
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    return _read(path, max(0, size - TAIL_BYTES), TAIL_BYTES)


def _title(entries: list[dict]) -> str:
    for entry in reversed(entries):
        if entry.get("type") == "ai-title" and entry.get("aiTitle"):
            return clip(str(entry["aiTitle"]), TITLE_MAX)
    return ""


def _last_answer(entries: list[dict]) -> str:
    """Text of the latest assistant message that has any (tool calls have none)."""
    for entry in reversed(entries):
        message = entry.get("message")
        if entry.get("type") != "assistant" or not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        texts = [c.get("text", "") for c in content or []
                 if isinstance(c, dict) and c.get("type") == "text"]
        if any(texts):
            return "\n".join(texts)
    return ""


def session_title(transcript_path: str) -> str:
    return _title(_tail(transcript_path)) if transcript_path else ""


def _cwd(path) -> str:
    for entry in _read(path, 0, HEAD_BYTES):
        if entry.get("cwd"):
            return str(entry["cwd"])
    return ""


def recent_sessions(exclude: set, method: str) -> list[dict]:
    """The latest sessions of this Mac, newest first: id, folder, title, age
    in seconds and the last summary. exclude: ids of sessions open right now."""
    now = time.time()
    candidates = []
    for path in PROJECTS_DIR.glob("*/*.jsonl"):
        session_id = path.stem
        if not SESSION_ID_RE.fullmatch(session_id) or session_id in exclude:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= RECENT_DAYS * 86400:
            candidates.append((mtime, path))

    result = []
    for mtime, path in sorted(candidates, reverse=True):
        cwd = _cwd(path)
        if not cwd or cwd.startswith(TEMP_PREFIXES) or not os.path.isdir(cwd):
            continue
        entries = _tail(path)
        answer = _last_answer(entries)
        if not answer:
            continue  # opened and closed without a single answer
        result.append({
            "id": path.stem,
            "cwd": cwd,
            "title": _title(entries),
            "age": int(now - mtime),
            "text": summarize(answer, method),
        })
        if len(result) == RECENT_LIMIT:
            break
    return result


def is_session_id(value: str) -> bool:
    return bool(SESSION_ID_RE.fullmatch(value or ""))

