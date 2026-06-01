# UCW Workflow

This is the operational guide to the 5-phase workflow UCW enforces.
Run it from the *user* perspective; for design rationale see `architecture.md`.

---

## When to use the workflow

| Situation | What to do |
|---|---|
| Trivial single-file edit ("fix typo in README") | Skip Scope+Plan. Edit → Verify → commit. |
| Multi-file feature, unclear scope | Run `/ucw plan <goal>`. Walk all 5 phases. |
| Bug with known root cause | Skip Scope. Run `/ucw plan` with the cause stated. |
| Refactor / migration | Always full 5 phases — these bite hard when rushed. |
| Hotfix to prod | Use full phases but compress: spec is 1 line, plan is 1-2 tasks. |

---

## The 5 phases

```
  ┌─────────┐    ┌────────┐    ┌────────┐    ┌─────────┐    ┌──────┐
  │  Scope  │ ─► │  Plan  │ ─► │ Build  │ ─► │ Verify  │ ─► │ Land │
  └─────────┘    └────────┘    └────────┘    └─────────┘    └──────┘
       │              │             │              │            │
   spec OK?      plan OK?       gates green?   review OK?   scribe
       │              │             │              │            │
       └──────────────┴─────────────┴──────────────┴────────────┘
                   blocking phase transitions
```

### 1. Scope — what + why (no code)

Owned by the **planner** subagent.

Output:
- One paragraph: what's being built and why
- 3-5 bullet success criteria (testable / observable)
- Risks / unknowns

Phase advance is blocked until the user approves the spec.

Phase tracker: `.ucw/state/phase = scope` (set via `bin/ucw-phase.py set scope`).

### 2. Plan — task decomposition (still no code)

Same agent. Output is a task list. Each task has:
- 1-line goal
- File path(s) to edit
- Verification step (test command, observable behavior)
- Parallel-safe group label

Example:
```
1. [A] Add /healthz endpoint    src/routes/health.py     verify: curl localhost/healthz returns 200
2. [A] Add healthz test         tests/test_health.py     verify: pytest tests/test_health.py
3. [B] Wire route in app        src/app.py               verify: pytest && app starts
```

Phase tracker: `.ucw/state/phase = plan`. The approved task list lands in
`.ucw/state/plan.md`. Plan items in the same parallel-group label CAN run in
parallel git worktrees via `bin/ucw-worktree.py create <slug>`.

### 3. Build — the work

Owned by **implementer**. Optionally delegates to `{lang}-expert` for
idiom-heavy edits.

**Inner-loop gates** fire on every Edit/Write via `hooks/post-tool-use.py`:
- Python files: `python -m py_compile` for syntax
- JSON: parse check
- YAML: parse check if PyYAML available
- Edit streak counter increments

**Streak breaker**: after 5 edits with no test run, `hooks/post-tool-batch.py`
refuses to let the agent stop until tests have been invoked. Reset by any
Bash call containing `pytest`, `vitest`, `jest`, `go test`, `cargo test`,
`rspec`, or `mocha`.

**Stop guard**: while `phase = build` and `edit-streak > 0`,
`hooks/stop.py` blocks Stop with a checklist of what's red.

Phase tracker: `.ucw/state/phase = build`.

### 4. Verify — gates

Owned by **verifier** subagent (read-only).

Gate suite in order, stops at first failure unless `--all`:

| Gate | What it checks |
|---|---|
| lint | From `PREFERENCES.formatter` / detected linter |
| types | `tsc --noEmit`, `mypy`, `cargo check`, etc. |
| tests | `PREFERENCES.test_runner` against changed scope |
| security | `bin/ucw-audit.py` + dependency scan |
| custom | Anything declared in `.ucw/evals/` |

Output is a colored gate report; non-zero exit blocks Land.

Optional: **reviewer** subagent then does spec-compliance + code-quality
review on the diff. Critical findings block Land.

Phase tracker: `.ucw/state/phase = verify`.

### 5. Land — commit, push, refresh Knowledge

Owned by **implementer**, then **scribe**.

1. `git add` (specific files per plan, or `-A`)
2. `git commit` with message derived from plan goal + task titles
3. `git push -u origin HEAD` unless `--no-push`
4. Optional PR via `mcp__github__create_pull_request`
5. **Scribe** invokes `bin/ucw-knowledge-diff.py` to see what changed and
   updates `.ucw/knowledge/*.md` accordingly
6. Memory flush via `hooks/stop.py` + distillation on `SessionEnd`
7. Phase cleared

---

## Phase tracker cheatsheet

```bash
ucw-phase.py get             # current phase or null
ucw-phase.py set scope       # only valid: scope|plan|build|verify|land
ucw-phase.py clear           # back to no phase
```

Phase lives at `.ucw/state/phase`. Hooks read it to decide whether to
block. Slash commands write it.

---

## When things go wrong

| Symptom | Probable cause | Fix |
|---|---|---|
| Stop blocked with "edit streak > 0" | Made changes without running tests | Run tests. The streak resets on any test invocation. |
| Stop blocked with "Verify must pass" | In build phase, gates haven't been run | Run `/ucw ship` (or the verifier directly). |
| `/ucw plan` keeps re-asking the same Scope question | Last session ended mid-Scope | `ucw-phase.py clear` then start fresh. |
| Scribe drowning DESIGN.md in churn | `PREFERENCES.scribe_mode` is too aggressive | Set to `additive-only` or `propose-only`. |
| Recall returns the wrong fact | FTS-only retrieval; need semantic match | Set `ANTHROPIC_API_KEY` for Claude reranker, or `VOYAGE_API_KEY` for Voyage. |
| `make smoke` fails at step 6 | DB path / cwd mismatch | The smoke script `cd`s into the project dir before MCP recall — re-check that `.ucw/memory.sqlite` exists there. |

---

## Worktree fan-out (parallel agents)

For plans with multiple parallel-safe groups, spawn a worktree per group:

```bash
ucw-worktree.py create task-a
# … run agent in that worktree's branch …
ucw-worktree.py list
ucw-worktree.py cleanup --max-age 24h   # remove old idle ones
```

Each worktree gets its own branch `ucw/wt/<slug>-<hex>` and meta file at
`.ucw/worktree-meta.json`. Cleanup refuses to remove worktrees with unpushed
commits unless `--force`.

---

## CI integration

Add this to your project's CI:

```yaml
- name: UCW self-audit
  run: python3 $HOME/.claude/ucw/bin/ucw-audit.py --repo .
- name: Stale-doc gate
  run: python3 $HOME/.claude/ucw/bin/ucw-knowledge-check.py --repo . --strict
```

The audit returns exit 2 on critical findings; the knowledge-check returns
exit 1 on any stale doc with `--strict`.
