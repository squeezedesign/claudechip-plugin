---
name: pair
description: Pair a Claude Chip device using the 6-digit code shown on its screen.
argument-hint: <6-digit code>
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh *)
---
Result of pairing the Claude Chip device:

!`${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh pair $ARGUMENTS`

Tell the user the result above in one short sentence, in their language. If the bridge is not running, explain that it starts with the next Claude Code session, then they can run this again.
