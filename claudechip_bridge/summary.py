"""Short summaries of Claude's answers for the device bubble (~100 chars)."""

from __future__ import annotations

import re

MAX_CHARS = 100

# [DECK] summary, or [DECK?] when the answer ends with a yes/no question
_DECK_RE = re.compile(r"^\s*\[DECK(\??)\]\s*(.+?)\s*$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_RE = re.compile(r"[*_`#>|]+")


def summarize(message: str, method: str) -> str:
    message = message or ""
    if method == "deck_line":
        deck = deck_line(message)
        if deck:
            return deck
    return truncate(message)


def deck_line(message: str) -> str | None:
    """The last '[DECK] ...' line of the answer, if any."""
    matches = _DECK_RE.findall(message)
    return clip(matches[-1][1]) if matches else None


def deck_question(message: str) -> bool:
    """True when the last summary line is '[DECK?]': a yes/no question."""
    matches = _DECK_RE.findall(message or "")
    return bool(matches) and matches[-1][0] == "?"


def truncate(message: str) -> str:
    """First sentence of the answer without markdown, cut to MAX_CHARS."""
    text = _CODE_BLOCK_RE.sub(" ", message)
    text = _MARKDOWN_RE.sub("", text)
    text = " ".join(text.split())
    if not text:
        return "listo."
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return clip(match.group(1) if match else text)


def clip(text: str, limit: int = MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"
