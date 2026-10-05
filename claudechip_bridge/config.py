"""Bridge configuration, read from a TOML file."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BRIDGE_DIR / "config.toml"

SUMMARY_METHODS = ("deck_line", "truncate")
USAGE_SOURCES = ("auto", "statusline")


@dataclass
class Config:
    account: str = "SQUEEZE"
    mac: str = "MAC"
    color: str = "#D7DF23"
    port: int = 8765
    summary: str = "deck_line"
    usage_source: str = "auto"
    dev_token: str = ""  # obsolete since pairing; accepted and ignored

    @property
    def hook_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook/"


def load(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG
    if not path.exists():
        raise SystemExit(
            f"config not found: {path}\n"
            f"copy {BRIDGE_DIR / 'config.example.toml'} to {DEFAULT_CONFIG} and edit it"
        )
    with path.open("rb") as f:
        raw = tomllib.load(f)

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
    return cfg
