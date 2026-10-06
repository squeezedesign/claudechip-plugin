"""Bridge configuration, read from a TOML file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib
except ImportError:  # the system python3 on macOS is 3.9
    tomllib = None

BRIDGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BRIDGE_DIR / "config.toml"  # manual install (git clone)


def plugin_config_path() -> Path:
    """Written by the plugin's SessionStart hook from the user's plugin options."""
    from .identity import DATA_DIR
    return DATA_DIR / "plugin-config.toml"

SUMMARY_METHODS = ("deck_line", "truncate")
USAGE_SOURCES = ("auto", "statusline")
OPEN_IN = ("auto", "iterm", "terminal", "phpstorm")


@dataclass
class Config:
    account: str = "SQUEEZE"
    mac: str = "MAC"
    color: str = "#D7DF23"
    port: int = 8765
    summary: str = "deck_line"
    usage_source: str = "auto"
    open_in: str = "auto"  # where "resume" opens a session (SPEC phase 5)
    dev_token: str = ""  # obsolete since pairing; accepted and ignored

    @property
    def hook_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook/"


def parse_simple_toml(text: str) -> dict:
    """The subset of TOML the config uses: key = "string" | integer | bool,
    and comments. Enough for Python 3.9, which has no tomllib."""
    result = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line, in_string = "", False
        for ch in raw:  # strip comments outside strings ("#D7DF23" is a value)
            if ch == '"':
                in_string = not in_string
            elif ch == "#" and not in_string:
                break
            line += ch
        line = line.strip()
        if not line:
            continue
        key, sep, value = (part.strip() for part in line.partition("="))
        if not sep or not key:
            raise SystemExit(f"config line {number}: expected key = value")
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            result[key] = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif value in ("true", "false"):
            result[key] = value == "true"
        else:
            try:
                result[key] = int(value)
            except ValueError:
                raise SystemExit(f"config line {number}: unsupported value {value!r}") from None
    return result


def load(path: Optional[Path] = None) -> Config:
    if path is None:
        path = DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else plugin_config_path()
    if not path.exists():
        raise SystemExit(
            f"config not found: {path}\n"
            f"copy {BRIDGE_DIR / 'config.example.toml'} to {DEFAULT_CONFIG} and edit it "
            f"(with the Claude Code plugin it is written for you when a session starts)"
        )
    if tomllib:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    else:
        raw = parse_simple_toml(path.read_text())

    known = set(Config.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise SystemExit(f"unknown config keys: {', '.join(sorted(unknown))}")
    cfg = Config(**raw)

    cfg.account = cfg.account.strip().upper()
    cfg.mac = cfg.mac.strip().upper()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", cfg.color):
        raise SystemExit(f"color must look like #RRGGBB, got {cfg.color!r}")
    if cfg.summary not in SUMMARY_METHODS:
        raise SystemExit(f"summary must be one of {SUMMARY_METHODS}, got {cfg.summary!r}")
    if cfg.usage_source not in USAGE_SOURCES:
        raise SystemExit(f"usage_source must be one of {USAGE_SOURCES}, got {cfg.usage_source!r}")
    if cfg.open_in not in OPEN_IN:
        raise SystemExit(f"open_in must be one of {OPEN_IN}, got {cfg.open_in!r}")
    return cfg
