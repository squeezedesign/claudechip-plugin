# Claude Chip — bridge

Companion for the Claude Chip desk device. It will be packaged as a Claude Code
plugin (see "Roadmap"); for now it is installed by hand.

Runs on each Mac. Receives Claude Code events through hooks, keeps the state of
every session and talks to the device over a WebSocket on the LAN (announced by
mDNS as `_claudechip._tcp`).

## Setup

```sh
brew install uv
git clone https://github.com/<owner>/claudechip-plugin && cd claudechip-plugin
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
- **Usage**: with `usage_source = "auto"` the bridge reads plan usage straight
  from Anthropic every 2 minutes, with the Claude Code login from the Keychain
  (the same data as `/usage`; undocumented endpoint). Check it with
  `uv run claudechip-bridge usage-check`. Otherwise, or if that fails, the status line wrapper forwards `context_window.used_percentage`
  (CTX) and `rate_limits.five_hour` / `seven_day` (SES / SEM, Pro and Max plans)
  and then runs the previous status line command.
- **Summaries**: `deck_line` takes the `[DECK] …` line that the rule in
  `~/.claude/CLAUDE.md` asks for; `truncate` uses the first sentence.

## Testing without the device

```sh
uv run python scripts/fake_device.py --token <dev_token> --answer ask
```

## Protocol (JSON over WebSocket)

Bridge → device: `hello` (account, mac, color), `state` (usage + sessions),
`permission`, `permission_cancel`, `summary`. Device → bridge: `hello` with the
token, `decision`. Every device message carries the token.

## Security

The bridge only listens on the local network and rejects messages without a
valid token. Never expose it to the internet.

## Roadmap

- Pairing with a 6-digit code shown on the device (replaces `dev_token`).
- Packaging as a Claude Code plugin: hooks bundled, `userConfig` for account /
  Mac / color, `[DECK]` rule injected at session start, bridge started in the
  background, `/claudechip:pair <code>`.

## License

Source available, not open source: you may install and use the bridge unmodified,
but not modify or redistribute it. See [LICENSE](LICENSE).
© 2026 [Squeeze Design](https://squeezedesign.es)
