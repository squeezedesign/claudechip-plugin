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

TAIL_BYTES = 256 * 1024  # the last answers are always near the end
DEFAULT_WINDOW = 200_000
LARGE_WINDOW = 1_000_000
# Models whose standard window is 1M even without the "[1m]" suffix
LARGE_WINDOW_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5")


def window_for(model: str, used: int) -> int:
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
