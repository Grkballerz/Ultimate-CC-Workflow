---
description: Run Verify then Land — gates first, then commit, push, and refresh Knowledge. Will NOT proceed if any gate fails.
argument-hint: "[--no-push] [--pr]"
---

1. Invoke the **verifier** subagent. Block on any gate failure with the report visible.
2. Invoke the **reviewer** subagent against the diff. Block on any critical finding.
3. If both pass:
   - `git add -A` (or specific files if `.ucw/state/plan.md` lists them)
   - `git commit` with a message derived from the plan goal + key task titles
   - Unless `--no-push`: `git push -u origin HEAD`
   - If `--pr`: open a PR via `mcp__github__create_pull_request`
4. Invoke the **scribe** subagent with the just-made diff to refresh `.ucw/knowledge/*`.
5. Print the commit SHA + the Knowledge files that changed.
