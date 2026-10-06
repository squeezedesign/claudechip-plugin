# Claude Chip — Claude Code plugin

Connects Claude Code to the **Claude Chip** desk device: it shows what each
session is doing, short summaries of every answer, and lets you approve or
deny permission requests with physical keys.

It runs a small bridge on your Mac (one per Mac) that receives Claude Code
events through hooks and talks to the device over the local network. No
dependencies: it uses the `python3` that ships with macOS and macOS's
`dns-sd`. macOS only for now.

## Install

```sh
claude plugin marketplace add squeezedesign/claudechip-plugin
claude plugin install claudechip@claudechip
```

Then, in Claude Code, open `/plugin`, choose **claudechip** and set its options:

| Option | What it is |
|---|---|
| Account name | Shown on the device, e.g. `SQUEEZE` or `EGLUU`. Macs with the same name are grouped |
| This Mac's name | Shown under the account in the device's selector (empty: the computer name) |
| Account color | Hex color of the account's tabs and name, e.g. `#D7DF23` |
| Summaries | `deck_line` (Claude ends each answer with a `[DECK]` line) or `truncate` |
| Plan usage | `auto` (read from Anthropic with your Claude Code login; macOS asks once for Keychain access) or `statusline` |
| Open sessions in | Where a session resumed from the device opens: `auto` (iTerm2 if installed, else Terminal), `iterm`, `terminal` or `phpstorm` (opens the project and copies the command) |
| Port | Local port of the bridge (default 8765) |

Start a new Claude Code session: the bridge starts by itself in the background
and the device finds it on the network.

## Commands

| Command | |
|---|---|
| `/claudechip:pair 482913` | Pair the device showing that code (first time, or from ⚙ AJUSTES → EMPAREJAR) |
| `/claudechip:status` | Is the bridge running, is the device connected |
| `/claudechip:devices` | Paired devices; `/claudechip:devices revoke <id>` forgets one |
| `/claudechip:setup` | Wrap your status line so terminal sessions report plan usage and context, and start the bridge when you log in (`remove` undoes both). Also cleans up a manual install |

## How it works

- **Hooks** forward every Claude Code event to the bridge on `127.0.0.1`. They
  run async and stay silent when the bridge is not running, so Claude Code is
  never slowed down or interrupted.
- **Permissions** wait, with no timeout, for the device or the terminal: the
  first answer wins. A device answer comes back as allow / deny; a terminal
  answer is noticed and the request is withdrawn from the device.
- **Questions and plan approvals** cannot be answered from the device (Claude
  Code offers no hook for them): the device beeps and asks you to answer in
  the console.
- **Summaries**: with `deck_line`, a short rule added at session start asks
  Claude to end each answer with `[DECK] …`; otherwise the first sentence.
- **History**: the bridge keeps the last 20 summaries of each session; hold
  the encoder to browse them.
- **Recent sessions**: `↺ RECIENTES` lists the latest sessions of this Mac
  (from `~/.claude/projects`); OK opens `claude --resume <id>` in its folder.
  The device can only pick one from the list the bridge sent; the folder and
  the command never come from it. The first time, macOS asks whether the
  bridge may control iTerm / Terminal.
- **Usage**: plan usage (SES / SEM) straight from Anthropic every 5 minutes,
  or from the status line; context (CTX) from each session's transcript.
- **State** lives in `~/.config/claudechip/`: paired devices, sessions,
  `bridge.log`.

## Pairing and security

The first time, the device shows a 6-digit code: type `/claudechip:pair <code>`.
Each device gets its own token, sent only once and kept in
`~/.config/claudechip/devices.json` and in the device. Afterwards both sides
prove they know it with HMAC-SHA256 over a random per-connection nonce, and
every device message is signed with a growing counter, so captured messages
cannot be forged or replayed. The bridge never logs the code: read it on the
device itself.

The bridge only listens on the local network and accepts only paired devices
and signed messages. Never expose it to the internet.

## Manual install (without the plugin)

```sh
git clone https://github.com/squeezedesign/claudechip-plugin && cd claudechip-plugin
cp config.example.toml config.toml        # account, mac, color
/usr/bin/python3 -m claudechip_bridge install     # hooks + status line in ~/.claude
/usr/bin/python3 -m claudechip_bridge             # run the bridge (Ctrl+C to stop)
```

`… uninstall` undoes `install`. Other commands: `pair <code>`, `devices`,
`revoke <id>`, `status`, `usage-check`. Do not use the manual install and the
plugin at the same time: events would be sent twice. `/claudechip:setup`
removes the manual hooks for you.

## Protocol (JSON over WebSocket)

Bridge → device: `hello` (bridge id, account, mac, color, nonce), `auth_ok`,
`auth_fail`, `paired` (token, once), `revoked`, `state` (usage + sessions),
`permission`, `permission_cancel`, `summary`, `history`, `recent`,
`resume_result`. Device → bridge: `auth` (HMAC), `pair_request` (code), then
signed `{"seq", "msg", "sig"}` envelopes carrying `decision`, `history_get`,
`recent_get` or `resume`.

## Development

```sh
claude plugin validate .
claude --plugin-dir . -p "hi"                                       # load it without installing
uv run --with aiohttp python scripts/fake_device.py --answer ask    # a fake device
```

## License

Source available, not open source: you may install and use the plugin
unmodified, but not modify or redistribute it. See [LICENSE](LICENSE).
© 2026 [Squeeze Design](https://squeezedesign.es)
