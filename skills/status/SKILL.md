---
name: status
description: Show whether the Claude Chip bridge is running on this Mac and whether the device is connected.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh *)
---
!`${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh status`

Summarize the status above for the user in one or two short sentences, in their language.
