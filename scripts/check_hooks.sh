#!/bin/sh
# Runs every hook script the way Claude Code does (from its own folder, with
# the system python3, against a port where nothing listens) and fails if one
# crashes. Catches mistakes like a module shadowing the standard library.
#   scripts/check_hooks.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
export CLAUDE_PLUGIN_ROOT="$ROOT" CLAUDE_PLUGIN_OPTION_PORT=9 CLAUDECHIP_DATA_DIR="$TMP"
EVENT='{"session_id":"check","cwd":"/tmp","hook_event_name":"Check"}'
fail=0
run() {
  name="$1"; shift
  if out=$(echo "$EVENT" | "$@" 2>&1) && ! echo "$out" | grep -q "Traceback"; then
    echo "ok    $name"
  else
    echo "FAIL  $name"; echo "$out" | sed 's/^/      /'; fail=1
  fi
}
run "hook.sh"       "$ROOT/scripts/hook.sh" Stop
run "forward.py"    /usr/bin/python3 "$ROOT/claudechip_bridge/forward.py"
run "statusline.py" /usr/bin/python3 "$ROOT/claudechip_bridge/statusline.py" 9
run "cli.sh status" "$ROOT/scripts/cli.sh" status
# launch.py would start a bridge: only check that it imports and parses options
run "launch.py imports" /usr/bin/python3 -c "import runpy, sys; sys.argv=['launch']; m = runpy.run_path('$ROOT/scripts/launch.py', run_name='check'); print(m['desired_config']())"
rm -rf "$TMP"
exit $fail
