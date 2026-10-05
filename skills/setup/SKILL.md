---
name: setup
description: Set up the status line so the Claude Chip shows plan usage and context from terminal sessions ("remove" to undo). Also cleans up a previous manual install.
argument-hint: "[remove]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh *)
---
!`${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh setup-plugin $ARGUMENTS`

Tell the user what changed, briefly and in their language. Their previous status line keeps working: it is wrapped, not replaced.
