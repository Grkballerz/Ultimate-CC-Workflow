---
description: Promote high-confidence instincts (recurring patterns from session distillation) into real Skill files for review.
---

Run the instinct distiller via Bash:

```
$HOME/.claude/ucw/bin/ucw-distill-instincts.py --min-uses 3 --promote 0.7
```

This:
1. Aggregates facts in `.ucw/memory.sqlite` by (predicate, object-token)
2. Upserts the instincts table for any pattern with ≥ min-uses
3. Drafts a `skills/promoted/<slug>/SKILL.md` for each pattern above
   `--promote` confidence
4. Prints what was inserted, updated, and drafted

After the helper finishes, walk each drafted skill file with the user and
ask whether to **accept** (keep), **edit** (open and refine), or **reject**
(delete). Use AskUserQuestion for the choice. On accept, the skill stays in
`skills/promoted/<slug>/`; on reject, delete the directory.

Add `--dry-run` to preview without writing, `--json` for machine-readable output.
