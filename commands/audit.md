---
description: Security audit of UCW's own configuration — settings, hooks, MCP servers, agents. Adversarial three-role review. Exit 2 on critical findings.
argument-hint: "[--opus]"
---

Two passes:

1. **Fast deterministic scan** via Bash:
   ```
   $HOME/.claude/ucw/bin/ucw-audit.py --repo "$(pwd)" --json
   ```
   This catches: secret patterns (14 families), shell injection patterns,
   MCP servers that exec a shell, reviewer agents with write tools.

2. **Adversarial review** via the **security-reviewer** subagent — invoke it
   with scope: settings, hooks, MCP configs, agents/skills under this repo.

Report critical / major / minor / false-alarm counts. Exit non-zero on any
critical finding for CI use. The deterministic scan supports `audit-allow:
<rule>` suppression comments for illustrative examples.
