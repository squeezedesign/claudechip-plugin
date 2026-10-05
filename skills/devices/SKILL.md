---
name: devices
description: List the Claude Chip devices paired with this Mac, or revoke one ("revoke <id>").
argument-hint: "[revoke <device id>]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh *)
---
!`${CLAUDE_PLUGIN_ROOT}/scripts/cli.sh devices $ARGUMENTS`

Show the user the result above briefly, in their language. To forget a device they can run `/claudechip:devices revoke <id>`.
