---
description: UCW umbrella command. Sub-actions — status, init, prefs, watch.
argument-hint: "<status|init|prefs|watch> [args]"
---

Sub-action: $ARGUMENTS

- `status` → `python -m ucw.dashboard.cli status` (token budget, memory stats, open work item, stale-doc warnings)
- `init`   → invoke onboarder (see `/init`)
- `prefs [categories]` → invoke onboarder restricted to listed categories
- `watch <PR>` → subscribe via `mcp__github__subscribe_pr_activity`, auto-fix CI failures within bounded scope
