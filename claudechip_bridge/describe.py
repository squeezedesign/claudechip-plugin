"""Turn Claude Code tool calls into short Spanish phrases for the device."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from .summary import clip

_EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}


def _file(tool_input: dict) -> str:
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or ""
    return os.path.basename(path) or "un archivo"


def _command(tool_input: dict) -> str:
    cmd = (tool_input.get("command") or "").strip().splitlines()
    return cmd[0] if cmd else "un comando"


def activity(tool_name: str, tool_input: dict) -> str:
    """What the session is doing right now, e.g. 'editando auth.php'."""
    if tool_name == "Bash":
        text = f"ejecutando: {_command(tool_input)}"
    elif tool_name in _EDIT_TOOLS:
        text = f"editando {_file(tool_input)}"
    elif tool_name == "Read":
        text = f"leyendo {_file(tool_input)}"
    elif tool_name in ("Grep", "Glob"):
        text = "buscando en el código..."
    elif tool_name == "WebFetch":
        text = f"consultando {urlparse(tool_input.get('url', '')).netloc or 'la web'}"
    elif tool_name == "WebSearch":
        text = f"buscando: {tool_input.get('query', '')}"
    elif tool_name in ("Task", "Agent"):
        text = "lanzando un agente..."
    elif tool_name.startswith("mcp__"):
        text = f"usando {tool_name.split('__')[-1]}"
    else:
        text = f"usando {tool_name.lower()}"
    return clip(text)


def permission_question(tool_name: str, tool_input: dict) -> str:
    """The question shown in the bubble, e.g. 'quiero ejecutar: npm install. ¿vale?'."""
    if tool_name == "Bash":
        what = f"ejecutar: {_command(tool_input)}"
    elif tool_name in _EDIT_TOOLS:
        what = f"editar {_file(tool_input)}"
    elif tool_name == "WebFetch":
        what = f"consultar {urlparse(tool_input.get('url', '')).netloc or 'una web'}"
    elif tool_name.startswith("mcp__"):
        what = f"usar {tool_name.split('__')[-1]}"
    else:
        what = f"usar {tool_name.lower()}"
    # Keep the question mark visible even when the command is long
    return clip(f"quiero {what}", 90) + ". ¿vale?"
