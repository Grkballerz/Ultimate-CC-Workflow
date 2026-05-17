---
description: Run the planner — scope + plan, presented for approval before any code is touched.
argument-hint: "<goal>"
---

The goal: $ARGUMENTS

1. Set phase via Bash: `$HOME/.claude/ucw/bin/ucw-phase.py set scope`
2. Invoke the **planner** subagent for the Scope phase. Wait for user approval.
3. On Scope approval, advance: `$HOME/.claude/ucw/bin/ucw-phase.py set plan` and invoke the planner again for the Plan phase.
4. On Plan approval, advance: `$HOME/.claude/ucw/bin/ucw-phase.py set build` and write the final task list to `.ucw/state/plan.md` so the implementer can pick it up.

Do not proceed to Build until both Scope and Plan are user-approved.
