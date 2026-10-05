"""Plan usage straight from Anthropic, like the /usage command does.

Reads the Claude Code login from the macOS Keychain ("Claude Code-credentials")
and asks the usage endpoint. The token is only sent to api.anthropic.com and is
never logged or stored. The first time, macOS asks whether `security` may read
the item.

The endpoint is not officially documented and may change: callers fall back to
the status line data when this fails.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

KEYCHAIN_SERVICE = "Claude Code-credentials"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
SYSTEM_CA_FILE = "/etc/ssl/cert.pem"
WINDOWS = ("five_hour", "seven_day")


class UsageError(Exception):
    """Human-readable reason why usage could not be read."""


@dataclass
class Login:
    access_token: str
    expires_at: float  # epoch seconds, 0 if unknown
    subscription: str

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at


def read_login() -> Login:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UsageError(f"could not run security: {e}") from None
    if result.returncode != 0:
        raise UsageError("Keychain item not found or access denied "
                         "(is Claude Code logged in? did you allow access?)")
    try:
        oauth = json.loads(result.stdout)["claudeAiOauth"]
        return Login(
            access_token=oauth["accessToken"],
            expires_at=float(oauth.get("expiresAt") or 0) / 1000,
            subscription=str(oauth.get("subscriptionType") or "?"),
        )
    except (ValueError, KeyError, TypeError):
        raise UsageError("unexpected format of the Claude Code credentials") from None


def _epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0


def _ssl_context() -> ssl.SSLContext:
    """Python builds from python.org ship without CA certificates; fall back to
    the bundle macOS keeps in /etc/ssl/cert.pem so HTTPS works with any python3."""
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca") and os.path.exists(SYSTEM_CA_FILE):
        ctx.load_verify_locations(SYSTEM_CA_FILE)
    return ctx


def fetch_raw(login: Login) -> dict:
    """The endpoint's JSON answer (usage figures only, no credentials)."""
    if login.expired:
        raise UsageError("the Claude Code token has expired; it renews the next time you use Claude Code")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {login.access_token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise UsageError(f"usage endpoint answered HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise UsageError(f"usage endpoint unreachable: {e}") from None
    return data


def fetch_usage(login: Login) -> dict[str, tuple[float, float]]:
    """{"five_hour": (percent, resets_at), "seven_day": (...)}"""
    data = fetch_raw(login)
    usage = {}
    for window in WINDOWS:
        info = data.get(window)
        if not isinstance(info, dict):
            continue
        pct = info.get("utilization", info.get("used_percentage"))
        if pct is not None:
            usage[window] = (float(pct), _epoch(info.get("resets_at")))
    if not usage:
        raise UsageError(f"no usage windows in the answer (keys: {', '.join(data)})")
    return usage


def check(raw: bool = False) -> None:
    """`claudechip-bridge usage-check`: show what the bridge would read.
    With raw=True, also print the endpoint's JSON answer."""
    try:
        login = read_login()
        if raw:
            print(json.dumps(fetch_raw(login), indent=2, ensure_ascii=False))
        print(f"Claude Code login found (plan: {login.subscription})")
        if login.expires_at:
            print(f"token valid for {max(0, (login.expires_at - time.time()) / 60):.0f} more min")
        usage = fetch_usage(login)
    except UsageError as e:
        raise SystemExit(f"usage-check failed: {e}")
    names = {"five_hour": "Sesión actual", "seven_day": "Esta semana"}
    for window, (pct, resets_at) in usage.items():
        when = datetime.fromtimestamp(resets_at).strftime("%a %d %H:%M") if resets_at else "?"
        print(f"{names[window]:14} {pct:5.1f}% usado   (se restablece {when})")
    print("ok: the bridge can read your usage directly")
