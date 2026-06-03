---
description: UCW root command. All UCW workflows live under here as subcommands to avoid colliding with Claude Code built-ins (/plan, /init, /review are reserved).
argument-hint: "<subcommand> [args]   |   status | init | plan | ship | review | audit | recall | pin | scribe | distill | dashboard | watch | unwatch | worktree | phase | prefs | auto | help"
---

Arguments: $ARGUMENTS

# Dispatch

Inspect the FIRST token of $ARGUMENTS. Everything after it is forwarded.

If no subcommand given OR the first token is `help`, run **Help** at the
bottom of this file.

```
ucw <sub> [args...]
  status              snapshot: phase, streak, memory, knowledge, stale-doc count
  init                onboarder — render .ucw/knowledge/* + init memory DB
  prefs [categories]  re-run onboarder restricted to specific categories
  plan <goal>         planner subagent — Scope + Plan phases
  ship [flags]        verify + reviewer + land (commit/push/PR)
  review [flags]      cross-audit code review (narrow scope + disprover + reachability)
  audit               deterministic security scan + adversarial reviewer
  recall <query>      memory.recall via the MCP server
  pin <fact>          memory.pin
  scribe              refresh .ucw/knowledge/* from the latest diff
  distill             promote recurring fact patterns into Skill drafts
  dashboard           open dashboard CLI (status snapshot)
  watch <PR>          subscribe to PR activity events for autopilot
  unwatch <PR>        stop watching
  worktree <subcmd>   create | list | cleanup | remove (multi-agent fan-out)
  phase <subcmd>      get | set <name> | clear
  auto <subcmd>       on [level] | off | status — autonomous run mode
```

---

## status

```
python3 $HOME/.claude/ucw/lib/dashboard/cli.py status
$HOME/.claude/ucw/bin/ucw-knowledge-check.py --json   # appended to "stale" line
```

Report phase, edit streak, memory counts (project + global), embedding mode,
knowledge doc inventory, stale-doc warnings.

---

## init

Invoke the **onboarder** subagent. It:
1. Calls `$HOME/.claude/ucw/bin/ucw-detect-stack.py "$(pwd)"` to detect language/framework/stack.
2. Asks the user the preference questions documented in `agents/onboarder.md`.
3. Writes `.ucw/state/init.json`.
4. Calls `$HOME/.claude/ucw/bin/ucw-render-knowledge.py --state .ucw/state/init.json --project-root .` to produce all 7 Knowledge docs.
5. Initializes `.ucw/memory.sqlite` via `mcp__ucw-memory__memory.init`.

---

## prefs [categories]

Same as `init` but restricted to the listed preference categories
(e.g. `image_gen_tool`, `diagram_tool`, `embedding_provider`).

---

## plan <goal>

Goal: the rest of $ARGUMENTS after `plan`.

1. Check auto-mode: `AUTO_LEVEL=$($HOME/.claude/ucw/bin/ucw-auto.py level)`
2. Set phase: `$HOME/.claude/ucw/bin/ucw-phase.py set scope`
3. Invoke the **planner** subagent for the Scope phase.
   - If `AUTO_LEVEL >= 1`: planner auto-accepts its own spec and proceeds
     immediately to the Plan phase. Print the spec for transparency but do
     NOT pause for AskUserQuestion.
   - Otherwise: wait for explicit user approval before proceeding.
4. `$HOME/.claude/ucw/bin/ucw-phase.py set plan` and planner runs the Plan phase.
   - If `AUTO_LEVEL >= 1`: same — auto-accept the plan, print it, advance.
   - Otherwise: wait for approval.
5. `$HOME/.claude/ucw/bin/ucw-phase.py set build` and write the final task list to `.ucw/state/plan.md`.

In normal mode (`AUTO_LEVEL == 0`) do not proceed to Build until both Scope
and Plan are user-approved. Auto-mode level 1+ removes both gates so the
agent can run end-to-end through Build unattended.

---

## ship [--no-push | --pr]

1. Check auto-mode: `AUTO_LEVEL=$($HOME/.claude/ucw/bin/ucw-auto.py level)`
2. Phase: `$HOME/.claude/ucw/bin/ucw-phase.py set verify`
3. Invoke the **verifier** subagent. Block on any gate failure.
4. Invoke the **reviewer** subagent against the diff. Block on critical findings.
5. If both pass: `$HOME/.claude/ucw/bin/ucw-phase.py set land`
   - **At AUTO_LEVEL < 3**: confirm with user before committing
     (`AskUserQuestion` summarizing the diff + commit message).
   - **At AUTO_LEVEL >= 3**: skip confirmation — proceed directly to commit.
   - `git add -A` (or specific files from `.ucw/state/plan.md`)
   - `git commit` with a message derived from the plan + key tasks
   - Unless `--no-push`: `git push -u origin HEAD`
   - If `--pr` OR `AUTO_LEVEL >= 4`: open a PR via `mcp__github__create_pull_request` (PR C wires the full level-4 flow)
6. Invoke the **scribe** subagent on the just-made diff.
7. `$HOME/.claude/ucw/bin/ucw-phase.py clear`
8. Print the commit SHA + Knowledge files that changed.

---

## review [--full | --quick | --narrow <area> | --since <ref> | approve <id> | status | list]

Cross-audit review pipeline. **Empty args OR `--full` → run the full pipeline:**

1. `$HOME/.claude/ucw/bin/ucw-review.py scope --persist [--since $SINCE]`
2. Fan out to 9 narrow reviewer subagents (`reviewer-correctness`, `reviewer-injection`,
   `reviewer-deserialization`, `reviewer-auth`, `reviewer-performance`,
   `reviewer-data-loss`, `reviewer-api-compat`, `reviewer-tests`, `reviewer-docs`).
   Each invokes `ucw-review.py add-finding ...` to persist findings.
3. For each finding: spawn the **disprover** subagent (model: haiku — different from
   reviewers, that's the cross-audit invariant). Disprover cannot generate new
   findings. It calls `ucw-review.py disprove <id> <verdict> ...`.
4. For each security finding (injection / deserialization / auth):
   spawn the **reachability** subagent. Calls `ucw-review.py reachability <id> <verdict> ...`.
5. `$HOME/.claude/ucw/bin/ucw-review.py dedup`
6. `$HOME/.claude/ucw/bin/ucw-review.py gate` — exits 2 on unack'd effective-critical
7. `$HOME/.claude/ucw/bin/ucw-review.py summary --format markdown` for the user

Other subcommands:
- `--quick` — single reviewer pass + deterministic audit, no cross-audit
- `--narrow <area>` — only one concern (e.g. `--narrow injection`)
- `--since <ref>` — diff base (default: `main`, then `HEAD~1`)
- `approve <id> [--reason "..."]` — human ack for a critical finding
- `status` — gate snapshot, no agents
- `list` — every finding across every SHA

---

## audit

Two-pass security check of UCW's own config:

1. Deterministic: `$HOME/.claude/ucw/bin/ucw-audit.py --repo "$(pwd)" --json`
2. Adversarial: invoke the **security-reviewer** subagent (three-role: attacker → defender → auditor)

Exit non-zero on critical for CI use. Supports `audit-allow: <rule>` comments
for illustrative examples in docs.

---

## recall <query>

Call `mcp__ucw-memory__memory.recall` with:
- `query`: $ARGUMENTS minus the `recall` token
- `k=12`, `budget_chars=4000`, `scope=all`

Inject top hits into the conversation context.

---

## pin <fact>

Call `mcp__ucw-memory__memory.pin` with the most recently recalled fact id,
or prompt the user to disambiguate via AskUserQuestion.

---

## scribe

Invoke the **scribe** subagent. It:
1. Calls `$HOME/.claude/ucw/bin/ucw-knowledge-diff.py` to see changed files
2. Updates the affected Knowledge docs (STACK, DESIGN, CONVENTIONS, etc.)
3. Writes the head SHA to `.ucw/last-scribe-sha` so the next diff is bounded

---

## distill

```
$HOME/.claude/ucw/bin/ucw-distill-instincts.py --min-uses 3 --promote 0.7
```

Aggregates facts by (predicate, object-token), promotes recurring patterns
to `skills/promoted/<slug>/SKILL.md`. Walk the user through accept/edit/reject
for each draft via AskUserQuestion.

---

## dashboard

```
python3 $HOME/.claude/ucw/lib/dashboard/cli.py status
```

(Same as `status` — kept as an alias.)

---

## watch <PR>

Call `mcp__github__subscribe_pr_activity` for the given PR number. On CI
failure events, re-diagnose and push fixes within bounded scope. On
review-comment events, use AskUserQuestion if ambiguous.

## unwatch <PR>

Call `mcp__github__unsubscribe_pr_activity`.

---

## worktree <create | list | cleanup | remove> [args]

Forward to `$HOME/.claude/ucw/bin/ucw-worktree.py <subcmd> [args]`.

---

## phase <get | set <name> | clear>

Forward to `$HOME/.claude/ucw/bin/ucw-phase.py <subcmd> [args]`.

---

## auto <on [level] | off | status | level>

Forward to `$HOME/.claude/ucw/bin/ucw-auto.py <subcmd> [args]`.

Levels are cumulative — each adds to the prior:

- **1** — planner auto-accepts its own spec/plan (no AskUserQuestion gates)
- **2** — Stop hook retry loop on verify failure (cap 3 by default)
- **3** — auto-commit + auto-push when verify passes
- **4** — auto-open draft PR + subscribe to PR activity for CI autofix

`/ucw auto on` with no number defaults to level 4 (full autonomy).
`/ucw auto off` clears the state file. `UCW_AUTO_MODE=off` env var
short-circuits everything regardless of state — escape hatch if the agent
goes wrong and you need to stop it now without finding the right file.

After enabling, print the level + a one-line warning about what's being
delegated. After disabling, print confirmation.

---

## help

Print the dispatch table at the top of this file.

---

# Why one umbrella

Claude Code reserves `/plan`, `/init`, and `/review` as built-in commands.
A plugin defining a top-level command with the same name conflicts in ways
that depend on install path. Routing everything under `/ucw <sub>` makes
the namespace unambiguous: there is exactly one UCW root command, and
it can never collide with a future built-in unless Claude Code ships
`/ucw` itself.

The slash-command file is the only one this matters for. Subagents and
skills are already namespaced by the plugin loader.
