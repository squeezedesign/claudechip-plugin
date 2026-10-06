"""Context window usage (CTX) read from a session's transcript.

The status line reports it only for terminal sessions; the VS Code extension
has no status line. Every hook carries transcript_path, and each assistant
entry there records the tokens of the request, so the bridge computes the
same figure itself: input + cache creation + cache read tokens over the
context window size.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .identity import DATA_DIR

TAIL_BYTES = 256 * 1024  # the last answers are always near the end
DEFAULT_WINDOW = 200_000
LARGE_WINDOW = 1_000_000
# Models whose standard window is 1M even without the "[1m]" suffix; only a
# fallback for models no terminal session has reported yet
LARGE_WINDOW_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5")
WINDOWS_FILE = DATA_DIR / "context_windows.json"

_learned: Optional[dict] = None


def _model_key(model: str) -> str:
    # The status line may say "claude-sonnet-4-5[1m]", the transcript only the API id
    return model.split("[", 1)[0].strip()


def _learned_windows() -> dict:
    global _learned
    if _learned is None:
        try:
            _learned = json.loads(WINDOWS_FILE.read_text())
        except (OSError, ValueError):
            _learned = {}
    return _learned


def learn_window(model: str, size) -> None:
    """Remember the real window of a model, as reported by the status line,
    so VS Code sessions (no status line) get it right too."""
    key = _model_key(str(model or ""))
    if not key or not isinstance(size, int) or size <= 0:
        return
    learned = _learned_windows()
    if learned.get(key) == size:
        return
    learned[key] = size
    try:
        WINDOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
        WINDOWS_FILE.write_text(json.dumps(learned, indent=2) + "\n")
    except OSError:
        pass


def window_for(model: str, used: int) -> int:
    learned = _learned_windows().get(_model_key(model))
    if learned and used <= learned:
        return learned
    if used > DEFAULT_WINDOW or "[1m]" in model or model.startswith(LARGE_WINDOW_MODELS):
        return LARGE_WINDOW
    return DEFAULT_WINDOW


def context_percent(transcript_path: str) -> Optional[int]:
    """Percentage of the context window used by the last answer, or None."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None

    for line in reversed(tail.splitlines()):
        if '"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # the first line of the tail may be cut
        message = entry.get("message")
        if entry.get("type") != "assistant" or not isinstance(message, dict):
            continue
        usage = message.get("usage") or {}
        used = (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0))
        if not used:
            continue
        model = str(message.get("model", ""))
        return min(100, round(used * 100 / window_for(model, used)))
    return None
