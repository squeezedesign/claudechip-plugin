#!/bin/sh
# Claude Chip plugin hook: forwards one Claude Code event (JSON on stdin) to
# the local bridge. Quiet and successful even when the bridge is not running.
#   hook.sh <EventName>
PORT="${CLAUDE_PLUGIN_OPTION_PORT:-8765}"
curl -s -m 2 -X POST -H 'Content-Type: application/json' \
  -H "X-Claude-Pid: $PPID" -H "X-Claude-Project: $CLAUDE_PROJECT_DIR" \
  --data-binary @- "http://127.0.0.1:$PORT/hook/$1" >/dev/null 2>&1 || true
