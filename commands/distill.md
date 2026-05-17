---
description: Promote high-confidence instincts (recurring patterns from session distillation) into real Skill files for review.
---

1. Call `mcp__ucw-memory__instincts.list` filtered to `confidence >= 0.8` and `uses >= 3`.
2. For each candidate, draft a `SKILL.md` + `INSTRUCTIONS.md` under `skills/promoted/<slug>/`.
3. Present the draft to the user for accept / edit / reject.
4. On accept, mark the instinct as `promoted_to_skill_id = <id>` so it's not re-proposed.
