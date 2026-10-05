"""Install / uninstall the bridge integration in the user's Claude Code config.

install:
  - backs up ~/.claude/settings.json
  - adds hooks that forward every event to the local bridge
  - wraps the status line (the previous one keeps working)
  - with summary = "deck_line", adds the [DECK] rule to ~/.claude/CLAUDE.md
uninstall reverses all of it.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .config import Config

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS = CLAUDE_DIR / "settings.json"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
STATE_DIR = Path.home() / ".config" / "claudechip"
PREVIOUS_STATUSLINE = STATE_DIR / "statusline_previous.json"
WRAPPER = Path(__file__).resolve().parent / "statusline.py"
FORWARDER = Path(__file__).resolve().parent / "forward.py"
SYSTEM_PYTHON = "/usr/bin/python3"

# Hooks are commands that pipe the event JSON to the bridge with curl.
# "|| true" keeps Claude Code quiet when the bridge is not running (an http
# hook would show "hook error" on every event). Events that only report
# state run async so they never slow Claude down. PermissionRequest is
# synchronous and waits for the device or the terminal, whichever answers
# first; it uses forward.py so that Claude Code killing the hook on a terminal
# answer really closes the connection.
HOOK_EVENTS = {
    # event: (curl max seconds or None, async)
    "SessionStart": (2, True),
    "SessionEnd": (1, True),
    "UserPromptSubmit": (2, True),
    "PreToolUse": (2, True),
    "PostToolUse": (2, True),
    "PostToolUseFailure": (2, True),
    "PermissionRequest": (None, False),
    "Notification": (2, True),
    "Stop": (2, True),
    "StopFailure": (2, True),
}
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}
PERMISSION_HOOK_TIMEOUT = 86400  # Claude Code side limit for the waiting hook

DECK_START = "<!-- claudechip:deck:start -->"
DECK_END = "<!-- claudechip:deck:end -->"
DECK_RULE = f"""{DECK_START}
## Claude Chip

Termina SIEMPRE cada respuesta con una última línea con este formato exacto:

[DECK] <resumen>

El resumen va en español, en minúsculas, estilo telegráfico (frases muy cortas,
sin artículos si sobran), sin markdown y con 100 caracteres como máximo.
Ejemplos: `[DECK] tests ok. login arreglado.` · `[DECK] falta clave de api. ¿la paso?`
{DECK_END}
"""


def _load_settings() -> dict:
    if not SETTINGS.exists():
        return {}
    return json.loads(SETTINGS.read_text())


def _save_settings(settings: dict) -> None:
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(SETTINGS)


def _backup() -> Path | None:
    if not SETTINGS.exists():
        return None
    dest = SETTINGS.with_name(f"settings.json.claudechip-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(SETTINGS, dest)
    return dest


def _is_ours(hook: dict, cfg: Config) -> bool:
    return hook.get("type") == "command" and cfg.hook_base_url in str(hook.get("command", ""))


def _hook(cfg: Config, event: str) -> dict:
    max_time, run_async = HOOK_EVENTS[event]
    if max_time is None:
        command = f"{SYSTEM_PYTHON} {FORWARDER} {cfg.hook_base_url}{event}"
    else:
        command = (f"curl -s -m {max_time} -X POST -H 'Content-Type: application/json' "
                   f"--data-binary @- {cfg.hook_base_url}{event} || true")
    hook = {"type": "command", "command": command}
    if run_async:
        hook["async"] = True
    else:
        hook["timeout"] = PERMISSION_HOOK_TIMEOUT
    return hook


def _remove_our_hooks(settings: dict, cfg: Config) -> None:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [h for h in group.get("hooks", []) if not _is_ours(h, cfg)]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)


def _wrapper_command(cfg: Config) -> str:
    return f"{SYSTEM_PYTHON} {WRAPPER} {cfg.port}"


def install(cfg: Config) -> None:
    CLAUDE_DIR.mkdir(exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    settings = _load_settings()
    backup = _backup()

    # Hooks: drop any previous copy of ours, then add the current set
    _remove_our_hooks(settings, cfg)
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        group = {"hooks": [_hook(cfg, event)]}
        if event in TOOL_EVENTS:
            group = {"matcher": "*", **group}
        hooks.setdefault(event, []).append(group)

    # Status line: remember the user's own command once, then wrap it
    current = settings.get("statusLine")
    wrapper = _wrapper_command(cfg)
    if current and current.get("command") != wrapper and not PREVIOUS_STATUSLINE.exists():
        PREVIOUS_STATUSLINE.write_text(json.dumps(current, indent=2) + "\n")
    settings["statusLine"] = {"type": "command", "command": wrapper}

    _save_settings(settings)
    print(f"hooks and status line installed in {SETTINGS}")
    if backup:
        print(f"backup: {backup}")

    if cfg.summary == "deck_line":
        text = CLAUDE_MD.read_text() if CLAUDE_MD.exists() else ""
        if DECK_START not in text:
            CLAUDE_MD.write_text((text.rstrip() + "\n\n" if text.strip() else "") + DECK_RULE)
            print(f"[DECK] rule added to {CLAUDE_MD}")


def uninstall(cfg: Config) -> None:
    settings = _load_settings()
    backup = _backup()
    _remove_our_hooks(settings, cfg)

    if (settings.get("statusLine") or {}).get("command") == _wrapper_command(cfg):
        if PREVIOUS_STATUSLINE.exists():
            settings["statusLine"] = json.loads(PREVIOUS_STATUSLINE.read_text())
            PREVIOUS_STATUSLINE.unlink()
        else:
            settings.pop("statusLine")
    _save_settings(settings)
    print(f"hooks and status line removed from {SETTINGS}")
    if backup:
        print(f"backup: {backup}")

    if CLAUDE_MD.exists():
        text = CLAUDE_MD.read_text()
        if DECK_START in text:
            start, end = text.index(DECK_START), text.index(DECK_END) + len(DECK_END)
            rest = (text[:start] + text[end:]).strip()
            if rest:
                CLAUDE_MD.write_text(rest + "\n")
            else:
                CLAUDE_MD.unlink()
            print(f"[DECK] rule removed from {CLAUDE_MD}")
