---
description: Security audit of UCW's own configuration — settings, hooks, MCP servers, agents. Adversarial three-role review. Exit 2 on critical findings.
argument-hint: "[--opus]"
---

Invoke the **security-reviewer** subagent with scope:

- `~/.claude/settings.json`
- `~/.claude/ucw/hooks/*`
- `~/.claude/mcp.json`
- `~/.claude/rules/ucw/*`
- All agent and skill files in this repo

Report critical / major / minor / false-alarm counts. Exit non-zero on any critical for CI use.
