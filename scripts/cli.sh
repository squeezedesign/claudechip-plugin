#!/bin/sh
# Runs a claudechip_bridge command for the plugin's slash commands. Always
# exits 0 so Claude Code shows the message instead of aborting the command.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" && /usr/bin/python3 -m claudechip_bridge "$@" 2>&1
exit 0
