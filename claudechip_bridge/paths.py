"""Where each Claude Code account keeps its files, and where the bridge keeps its own.

Several accounts can share one Mac: Claude Code reads CLAUDE_CONFIG_DIR to
use another folder than ~/.claude (e.g. ~/.claude-personal). Each account
gets its own bridge, with its own data folder, LaunchAgent and Keychain item:

  ~/.claude          -> ~/.config/claudechip
  ~/.claude-personal -> ~/.config/claudechip-claude-personal

The bridge, hooks and slash commands all run under Claude Code (or the
LaunchAgent, which sets the variable), so they see the same CLAUDE_CONFIG_DIR.
Standard library only, Python 3.9+: statusline.py and autostart.py repeat
data_dir_for() because they run without the package.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
BASE_DATA_DIR = Path.home() / ".config" / "claudechip"


def claude_dir() -> Path:
    value = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(value).expanduser() if value else DEFAULT_CLAUDE_DIR


def is_default_account() -> bool:
    return os.path.normpath(str(claude_dir())) == os.path.normpath(str(DEFAULT_CLAUDE_DIR))


def data_dir_for(claude: Path) -> Path:
    if os.path.normpath(str(claude)) == os.path.normpath(str(DEFAULT_CLAUDE_DIR)):
        return BASE_DATA_DIR
    return BASE_DATA_DIR.with_name("claudechip-" + claude.name.lstrip(".").lower())


def data_dir() -> Path:
    # CLAUDECHIP_DATA_DIR keeps tests away from the real paired devices
    value = os.environ.get("CLAUDECHIP_DATA_DIR")
    return Path(value) if value else data_dir_for(claude_dir())


def keychain_service() -> str:
    """Claude Code's login item: one per config folder, suffixed with a hash of
    its path when it is not the default one."""
    if is_default_account():
        return "Claude Code-credentials"
    digest = hashlib.sha256(str(claude_dir()).encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


def agent_label() -> str:
    """LaunchAgent label: one per account, the default keeps its old name."""
    label = "es.squeezedesign.claudechip"
    if is_default_account():
        return label
    return f"{label}.{claude_dir().name.lstrip('.').lower()}"
