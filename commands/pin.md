---
description: Guarantee a fact is included in every future SessionStart injection. Use `--global` to apply across all projects.
argument-hint: "<fact> [--global]"
---

Call `mcp__ucw-memory__memory.pin` with:
- `fact`: $ARGUMENTS (strip `--global` flag if present)
- `scope`: `global` if `--global` was passed, else `project`

Confirm with the new pin count.
