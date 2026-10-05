# Claude Chip — bridge

Runs on each Mac. Receives Claude Code events through hooks, keeps the state of
every session and talks to the device over a WebSocket on the LAN (announced by
mDNS as `_claudechip._tcp`). See `../SPEC.md` §7.

## Setup

```sh
brew install uv
cd bridge
cp config.example.toml config.toml   # set account, mac, color, dev_token
uv sync
uv run claudechip-bridge install     # hooks + status line in ~/.claude (backup first)
uv run claudechip-bridge             # run it (Ctrl+C to stop)
```

`uv run claudechip-bridge uninstall` removes the hooks, restores the previous
status line and removes the `[DECK]` rule from `~/.claude/CLAUDE.md`.

## How it works

- **Hooks** (`~/.claude/settings.json`): every event is piped to
  `http://127.0.0.1:<port>/hook/<Event>` with `curl … || true`, async, so Claude
  Code is never slowed down and stays silent when the bridge is not running.
- **Permissions**: `PermissionRequest` waits, with no timeout, for the device or
  the terminal. A device answer is returned as `allow`/`deny`. A terminal answer
  is inferred from the next event of the session, and the request is withdrawn
  from the device.
- **Usage**: the status line wrapper forwards `context_window.used_percentage`
  (CTX) and `rate_limits.five_hour` / `seven_day` (SES / SEM, Pro and Max plans)
  and then runs the previous status line command.
- **Summaries**: `deck_line` takes the `[DECK] …` line that the rule in
  `~/.claude/CLAUDE.md` asks for; `truncate` uses the first sentence.

## Testing without the device

```sh
uv run python scripts/fake_device.py --token <dev_token> --answer ask
```
