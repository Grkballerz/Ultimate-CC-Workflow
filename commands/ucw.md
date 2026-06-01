---
description: UCW umbrella command. Sub-actions — status, init, prefs, watch.
argument-hint: "<status|init|prefs|watch> [args]"
---

Sub-action: $ARGUMENTS

Dispatch based on the first token:

- `status` → run Bash: `python3 $HOME/.claude/ucw/lib/dashboard/cli.py status`
  (or `--json` for machine output). Also surfaces stale Knowledge docs via
  `$HOME/.claude/ucw/bin/ucw-knowledge-check.py --json`.
- `init`   → invoke the **onboarder** subagent (`/init`).
- `prefs [categories]` → invoke onboarder restricted to listed categories.
- `watch <PR>` → call `mcp__github__subscribe_pr_activity` for the given PR
  number, then follow the autopilot routine: on CI failure events,
  re-diagnose and push fixes within bounded scope; on review-comment events,
  use AskUserQuestion if the suggestion is ambiguous. Call
  `mcp__github__unsubscribe_pr_activity` when the user asks to stop.
- `unwatch <PR>` → unsubscribe via `mcp__github__unsubscribe_pr_activity`.
- `worktree create|list|cleanup` → run `$HOME/.claude/ucw/bin/ucw-worktree.py <subcmd>`.
- `phase get|set|clear` → run `$HOME/.claude/ucw/bin/ucw-phase.py <subcmd>`.
