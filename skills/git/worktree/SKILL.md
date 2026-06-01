---
name: git-worktree
description: Use git worktrees to run parallel agents (or experiments) without touching the main checkout. Activate when the approved plan has multiple parallel-safe groups, when comparing implementations side-by-side, or when keeping a long-running refactor isolated.
when_to_use:
  - Plan has multiple parallel-safe groups labeled [A], [B], etc.
  - Comparing two implementation approaches
  - Long-running refactor that shouldn't block the main branch
when_not_to_use:
  - Single small change — overhead isn't worth it
  - You're already inside a worktree (don't nest)
---

# Git Worktree Usage

Worktrees give you a fresh checkout on a new branch, sharing the same `.git`
directory. UCW manages them under `.ucw/wt/<slug>-<hex>/` with the
`bin/ucw-worktree.py` helper.

## Create

```bash
$HOME/.claude/ucw/bin/ucw-worktree.py create add-healthz
# → creates .ucw/wt/add-healthz-a1b2c3/ on branch ucw/wt/add-healthz-a1b2c3
```

Optional `--base <ref>` overrides the parent (default: current HEAD).
A `.ucw/worktree-meta.json` is written inside the new tree recording the
parent ref and creation time.

## List

```bash
$HOME/.claude/ucw/bin/ucw-worktree.py list           # human-readable
$HOME/.claude/ucw/bin/ucw-worktree.py list --json    # machine-readable
```

Only UCW worktrees (under `ucw/wt/`) are shown.

## Cleanup

```bash
$HOME/.claude/ucw/bin/ucw-worktree.py cleanup --max-age 24h
$HOME/.claude/ucw/bin/ucw-worktree.py cleanup --max-age 7d --force
```

Refuses to remove worktrees with unpushed commits unless `--force`. Duration
formats: `30s | 5m | 2h | 1d`.

## Remove explicitly

```bash
$HOME/.claude/ucw/bin/ucw-worktree.py remove add-healthz
```

Always uses `--force` (you asked for it). Deletes the branch too.

## Multi-agent pattern

When the planner emits a task list with two parallel groups:

```
1. [A] Add /healthz route       src/routes/health.py
2. [A] Add healthz test         tests/test_health.py
3. [B] Add /readyz route        src/routes/ready.py
4. [B] Add readyz test          tests/test_ready.py
```

The orchestrator subagent spawns one worktree per group, fans out to two
implementer subagents (one per worktree), and merges back to the parent
branch only after both pass Verify.

## Hazards

- **Don't edit the same file in two worktrees.** Git won't help here.
- **Don't push from a worktree to a shared branch.** Push to the worktree's
  own branch; merge back via PR or fast-forward locally.
- **Cleanup unpushed work first.** `cleanup` without `--force` will refuse;
  that's protective, not annoying.
