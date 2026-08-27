---
description: UCW root command. All UCW workflows live under here as subcommands to avoid colliding with Claude Code built-ins (/plan, /init, /review are reserved).
argument-hint: "<subcommand> [args]   |   status | init | plan | ship | review | audit | ask | opinion | recall | pin | scribe | distill | dashboard | watch | unwatch | worktree | phase | prefs | auto | settings | help"
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
  ask <question>      repo-oracle subagent — answer questions about this repo
  opinion <q | --diff> ad-hoc Kimi second opinion (advisory, unverified, read-only)
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
  settings <subcmd>   list | get <key> | set <key> <value> | unset <key>
  resume              print resume block after /clear or /compact
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
5. Initializes `.ucw/memory.sqlite` via the CLI: `"$HOME/.claude/ucw/venv/bin/ucw-memory" init`
   (subagents have no MCP tools, so the onboarder must NOT be asked to call
   `mcp__ucw-memory__*` — that tool is only reachable from this main session).

After the onboarder returns: if `.ucw/memory.sqlite` does not exist (CLI was
missing or the step was skipped), call `mcp__ucw-memory__memory.init` yourself
from this session to complete it.

---

## prefs [categories]

Same as `init` but restricted to the listed preference categories
(e.g. `image_gen_tool`, `diagram_tool`, `embedding_provider`).

---

## plan <goal>

Goal: the rest of $ARGUMENTS after `plan`.

1. Check auto-mode: `AUTO_LEVEL=$($HOME/.claude/ucw/bin/ucw-auto.py level)`
2. Set phase: `$HOME/.claude/ucw/bin/ucw-phase.py set scope`
3. Prime memory: call `mcp__ucw-memory__memory.recall` with `query` = the goal
   (`k=8`, `budget_chars=3000`, `scope=all`) and include the hits in the
   planner's prompt. Subagents have no MCP tools — the planner cannot recall
   memory itself, so this step is how prior decisions reach the plan.
4. Invoke the **planner** subagent for the Scope phase.
   - If `AUTO_LEVEL >= 1`: planner auto-accepts its own spec and proceeds
     immediately to the Plan phase. Print the spec for transparency but do
     NOT pause for AskUserQuestion.
   - Otherwise: wait for explicit user approval before proceeding.
5. `$HOME/.claude/ucw/bin/ucw-phase.py set plan` and planner runs the Plan phase.
   - If `AUTO_LEVEL >= 1`: same — auto-accept the plan, print it, advance.
   - Otherwise: wait for approval.
6. `$HOME/.claude/ucw/bin/ucw-phase.py set build` and write the final task list to `.ucw/state/plan.md`.

In normal mode (`AUTO_LEVEL == 0`) do not proceed to Build until both Scope
and Plan are user-approved. Auto-mode level 1+ removes both gates so the
agent can run end-to-end through Build unattended.

### The `[kimi]` task tag

The planner MAY tag an individual plan task `[kimi]` to mark it as a
candidate for offload to Kimi K3 — but ONLY when the task is bounded and
mechanical: boilerplate, docs stubs, test scaffolding, and similar. NEVER
tag auth, security, or core-logic tasks `[kimi]`.

`[kimi]` tags are honored ONLY when `ucw-settings.py get kimi.offload`
resolves true (default: false). When it is false, the implementer treats
the task as a normal Claude task and the tag is inert — this is the
kill-switch that keeps unattended `/ucw auto` runs from spending Kimi
tokens.

When honored, the implementer routes the task to
`$HOME/.claude/ucw/bin/ucw-kimi-implement.py` (file edits only — Kimi
gets no shell), then MUST run its own verification. The Kimi diff is
unverified until the normal verifier + reviewer gates pass — the exact
same bar as a Claude-authored diff. Tick the task in `.ucw/state/plan.md`
only after that verification passes.

---

## ship [--no-push | --pr | --base <branch>]

1. Check auto-mode: `AUTO_LEVEL=$($HOME/.claude/ucw/bin/ucw-auto.py level)`
2. Phase: `$HOME/.claude/ucw/bin/ucw-phase.py set verify`
3. Spawn the **verifier** AND **reviewer** subagents IN PARALLEL — one
   message, two Task calls; neither depends on the other's output:
   - **verifier** — runs `$HOME/.claude/ucw/bin/ucw-verify.py --repo .`
     (never a hand-rolled gate suite). The CLI always writes its JSON
     result to `.ucw/state/verify-report.json`. A "cached PASS" served
     from the tree-keyed `.ucw/state/last-verify.json` cache is a VALID
     pass — at auto-level >= 3 the Stop-hook verify that triggered ship
     already recorded the cache entry, so ship's verify is normally a
     cache hit and costs ~0s.
   - **reviewer** — reviews the diff and WRITES its report to
     `.ucw/state/review-report.md`. Skip this spawn entirely when
     `.ucw/reviews/<HEAD-sha>/` already holds a passing review gate
     (`ucw-review.py gate` exits 0 for the current HEAD) — a full
     `/ucw review` already covered this exact tree; don't pay twice.
4. Read BOTH FILES — `.ucw/state/verify-report.json` and
   `.ucw/state/review-report.md`. The agents' return messages are
   pointers only; the files are the deliverable. Block on any failed
   gate (`"passed": false` in the verify report) and on any critical
   review finding.
5. If both pass: `$HOME/.claude/ucw/bin/ucw-phase.py set land`
   - **At AUTO_LEVEL < 3**: confirm with user before committing
     (`AskUserQuestion` summarizing the diff + commit message).
   - **At AUTO_LEVEL >= 3**: skip confirmation — proceed directly to commit.
   - `git add -A` (or specific files from `.ucw/state/plan.md`)
   - `git commit` with a message derived from the plan + key tasks
   - Unless `--no-push`: consult `ucw-settings.py get ship.push` — if it
     resolves false, behave exactly as if `--no-push` had been passed;
     otherwise (true, the default) `git push -u origin HEAD`
6. If `--pr` OR `ucw-settings.py get ship.pr` resolves true OR `AUTO_LEVEL >= 4`:
   a. Generate PR metadata: `META=$($HOME/.claude/ucw/bin/ucw-pr-meta.py --base ${ARG_BASE:-main})`
      - Exit 0 → use the JSON.
      - Exit 2 → no commits ahead of base, skip the PR step entirely (log it for the user).
      - Exit 1 → not a git repo / git failure, log error and continue.
   b. Call `mcp__github__create_pull_request` with `title`, `body`, `head` = `META.head_branch`,
      `base` = `META.base_branch`, `draft = true` (so reviewers see "draft" status until
      the human marks it ready).
   c. **At AUTO_LEVEL >= 4 only**: call `mcp__github__subscribe_pr_activity` for the new PR
      so CI failures and review comments wake this session for autopilot fixes. Same
      contract as `/ucw watch <PR>` — fix bounded issues automatically, AskUserQuestion
      on ambiguous comments.
7. Consult `ucw-settings.py get scribe.auto` — when true (the default),
   invoke the **scribe** subagent on the just-made diff; when false, skip
   this step (the user runs `/ucw scribe` manually later).
8. `$HOME/.claude/ucw/bin/ucw-phase.py clear`
9. Print the commit SHA, the PR URL (if opened), and Knowledge files that changed.

---

## review [--full | --quick | --narrow <area> | --since <ref> | --with-kimi | --disprover-model <haiku|kimi> | approve <id> | status | list]

Cross-audit review pipeline. **Empty args OR `--full` → run the full pipeline**
— except that with empty args (no `--full`/`--quick` given), first consult
`ucw-settings.py get review.default`: `full` (the default) runs the full
pipeline below; `quick` behaves exactly as if `--quick` had been passed.
An explicit flag always wins over the setting. Full pipeline:

1. `$HOME/.claude/ucw/bin/ucw-review.py scope --persist [--since $SINCE]`
   — the slimmed output reports `lanes` (concern → scope count) and
   persists the full (file, concern) list to `scope.json`.
2. Reviewer fan-out — IN PARALLEL: one message, one Task call per lane.
   Spawn ONLY the lanes whose concern appears in the scope output's
   `lanes` map with a non-zero count (a lane with no files in scope is
   neither spawned nor expected at the gate). The 9 possible lanes:
   `reviewer-correctness`, `reviewer-injection`,
   `reviewer-deserialization`, `reviewer-auth`, `reviewer-performance`,
   `reviewer-data-loss`, `reviewer-api-compat`, `reviewer-tests`,
   `reviewer-docs`. Each invokes `ucw-review.py add-finding ...` to
   persist findings and ALWAYS ends by recording its completion receipt:
   `ucw-review.py lane-done <lane>` (e.g. `lane-done correctness`).
   If the Kimi lane is enabled (see `--with-kimi` below), launch
   `$HOME/.claude/ucw/bin/ucw-kimi-opinion.py [--since $SINCE]` in the
   SAME parallel wave as a background lane alongside the reviewers —
   never serially after them. The Kimi lane is advisory and records no
   receipt: do NOT list it in `--expect-lanes`.
3. `$HOME/.claude/ucw/bin/ucw-review.py dedup` — runs BEFORE any
   disprover spawns so near-duplicate findings are merged first and a
   duplicate is never paid for twice.
4. Cross-audit wave — spawn IN PARALLEL, one message, all Task calls
   together:
   - a **disprover** subagent per surviving finding, for critical + major
     findings only (minor/nit are not worth a disprover invocation).
     Model: haiku — different from the reviewers, that's the cross-audit
     invariant. Disprover cannot generate new findings. It calls
     `ucw-review.py disprove <id> <verdict> ...`.
     If the disprove step is routed to Kimi (see `--disprover-model` below),
     run `$HOME/.claude/ucw/bin/ucw-kimi-disprove.py <id>` per finding instead.
   - a **reachability** subagent per security finding (injection /
     deserialization / auth), in this SAME wave — not a serial pass after
     the disprovers. Calls `ucw-review.py reachability <id> <verdict> ...`.
5. `$HOME/.claude/ucw/bin/ucw-review.py gate --expect-lanes <lanes>` where
   `<lanes>` is the comma-separated list of lanes you actually spawned in
   step 2 (e.g. `correctness,tests,docs`) — exits 2 on unack'd
   effective-critical; exits 3 listing lanes that never recorded a
   `lane-done` receipt, so a reviewer that died silently no longer looks
   identical to a clean lane.
6. `$HOME/.claude/ucw/bin/ucw-review.py summary --format markdown` for the
   user — includes the pending-disprove count (findings still awaiting a
   disprover verdict).

Optional flags (both opt-in, both default off):

- `--with-kimi` — adds the `kimi-second-opinion` lane: one whole-diff pass
  through the claude-kimi bridge (`bin/ucw-kimi-opinion.py`), launched in
  the SAME parallel fan-out wave as the reviewer subagents (background).
  Its findings are persisted via the same `add-finding` path and flow
  through the normal dedup → disprove → gate stages — no special
  treatment, except that it records no lane receipt (advisory lane;
  leave it out of `--expect-lanes`).
  Expect ~30-90s of extra wall-clock latency typically (the invocation
  timeout defaults to 300s, tunable via the `kimi.timeout_secs` setting)
  and note that Kimi tokens bill separately from Claude. When the
  API-key path times out or hits quota, the bridge auto-falls-back to
  the standalone `kimi` CLI (tool-less calls only — the `[kimi]`
  implementer offload never switches transports because the CLI cannot
  scope tools); see the `kimi.transport` setting. When the flag
  is absent, consult
  `ucw-settings.py get kimi.review` — if it resolves true, run the lane
  exactly as if `--with-kimi` had been passed.
- `--disprover-model kimi` — routes the disprove step (step 4) through
  `bin/ucw-kimi-disprove.py` so Kimi K3 is the opposing model. The
  default is unchanged: the haiku **disprover** subagent. When the flag
  is absent, consult `ucw-settings.py get kimi.disprover` — `haiku`
  (default) keeps the subagent; `kimi` routes through the bridge.
  The Kimi disprover runs with read-only tools (`Read,Grep,Glob` —
  no Bash): disproving is reading code, not running it.

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
for illustrative examples in docs. The token must name the rule being silenced
(or `*` / `all` for every rule on the line) — a bare or mismatched token does
not suppress. Suppression is only honored on UCW's own files, never on paths
passed via `--target` (treated as untrusted), and a suppressed match is still
reported as a visible `nit` so it never disappears silently.

---

## ask <question>

The "brains of the repo" — answer a question about THIS project, grounded in
the curated Knowledge docs, memory, and the actual code (not generic
knowledge). The question is the rest of $ARGUMENTS after the `ask` token.

1. Prime memory: `mcp__ucw-memory__memory.recall` with `query` = the question,
   `k=8`, `budget_chars=3000`, `scope=all`. (The subagent recalls again with
   its own framing, but seeding here surfaces obvious hits up front.)
2. Invoke the **repo-oracle** subagent with the verbatim question. It reads
   `.ucw/knowledge/INDEX.md` → relevant docs, recalls memory, greps/reads the
   code, and checks git history as needed — read-only throughout.
3. Print its answer verbatim: the direct answer, the cited **Evidence**,
   **Confidence**, and the **Couldn't determine** list.

This is a read-only query path — it never enters a workflow phase, never
edits, and never touches `.ucw/state`. If the oracle reports a Knowledge doc
is stale (docs disagree with code), suggest `/ucw scribe` to the user.

---

## opinion <question | --diff>

Ad-hoc second opinion from Kimi K3 — a cross-model sanity check on a
question or on the working diff. The question is the rest of $ARGUMENTS
after the `opinion` token; `--diff` sends `git diff` output instead.

1. Invoke the bridge. The PRIMARY path is a free-form prose answer:
   `claude-kimi -p "<question>"` directly, or equivalently
   `$HOME/.claude/ucw/bin/kimi_invoke.py "<question>" --raw`
   (`--raw` skips JSON extraction — any non-empty stdout is success).
   Prose answers are the normal case for an opinion; drop `--raw` only
   when you need a structured JSON payload extracted from the output.

   For `--diff`, pipe the diff in as the prompt via the stdin marker `-`:

   ```
   git diff | $HOME/.claude/ucw/bin/kimi_invoke.py - --raw
   ```

   Typical latency is ~30-90s; the invocation timeout defaults to 300s,
   tunable via the `kimi.timeout_secs` setting (or `UCW_KIMI_TIMEOUT_SECS`).
   When the API-key path times out or hits a quota/auth error, the bridge
   auto-falls-back to the standalone `kimi` CLI (subscription OAuth) for
   tool-less calls like this one — tunable via the `kimi.transport` setting.
2. Print the answer VERBATIM under an explicit banner:

   ```
   ## Kimi (advisory, unverified)
   <answer exactly as returned>
   ```

The answer is advisory only — cross-model input, not a verdict. Do not
merge it into your own voice, do not act on it without normal verification,
and label it clearly so the user knows which model said what.

Like `ask`, this is a read-only query path — it never enters a workflow
phase, never edits, and never touches `.ucw/state`.

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

## resume

Run `$HOME/.claude/ucw/bin/ucw-resume.py` and print the output verbatim.

This is the post-`/clear` / post-`/compact` re-orientation command. It
reads `.ucw/state/*` and renders a markdown block with: current phase,
auto-mode level + since + retry budget, persisted spec, persisted plan,
HEAD commit + branch, dirty-tree warning (if auto-mode is on and you have
uncommitted changes in `land` phase — i.e. crashed mid-ship), Knowledge
file inventory, and the most recent pre-compact digest.

The same hint (much shorter) is auto-injected by `user-prompt-submit.py`
on every prompt when workflow state is set, so you usually don't need to
run `resume` explicitly — it's there for when you want the full picture.

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

## settings [list | get <key> | set <key> <value> | unset <key>]

Forward to `$HOME/.claude/ucw/bin/ucw-settings.py <subcmd> [args]`.

Verbs: `list` (every key with effective value + source), `get <key>`,
`set <key> <value>`, `unset <key>` (revert to default).

Per-project behavior toggles stored at `.ucw/state/settings.json`. The key
registry is a closed set — settings never store secrets or credentials
(secrets belong in env vars managed outside UCW).

Key registry:

| key | type | default | effect |
|-----|------|---------|--------|
| `kimi.review` | bool | `false` | treat `--with-kimi` as default on `/ucw review` (consulted by the review dispatch when the flag is absent) |
| `kimi.disprover` | enum (`haiku` \| `kimi`) | `haiku` | disprover route (consulted by `/ucw review` step 3 when `--disprover-model` is absent; default `haiku` keeps the subagent, `kimi` routes via the bridge) |
| `kimi.offload` | bool | `false` | honor `[kimi]` plan-task tags (consulted by the implementer) — kill-switch that keeps unattended auto-mode runs from spending Kimi tokens |
| `kimi.model` | str | `kimi-k3` | model id passed to the claude-kimi bridge (consulted by `kimi_invoke` when `--model` is absent; exported to the subprocess as `KIMI_MODEL`) |
| `kimi.timeout_secs` | int | `300` | per-invocation timeout in seconds for any headless Kimi call (consulted by `kimi_invoke` when `--timeout` is absent) |
| `kimi.transport` | enum (`auto` \| `claude-kimi` \| `kimi-cli`) | `auto` | Kimi bridge transport (consulted by `kimi_invoke` when `--transport` is absent): `claude-kimi` = API-key wrapper, `kimi-cli` = standalone subscription CLI, `auto` = claude-kimi with kimi-cli fallback on timeout/api-error for tool-less calls |
| `review.default` | enum (`full` \| `quick`) | `full` | default review depth (consulted by the `/ucw review` dispatch when `--full`/`--quick` are absent) |
| `ship.push` | bool | `true` | push the branch after commit (consulted by the `/ucw ship` land step when `--no-push` is absent) |
| `ship.pr` | bool | `false` | open a draft PR after push (consulted by the `/ucw ship` PR step when `--pr` is absent) |
| `scribe.auto` | bool | `true` | run the scribe automatically after Land (consulted by the `/ucw ship` scribe step) |
| `auto.default_level` | int | `4` | auto-mode level 1-4 used by bare `/ucw auto on` (consulted by `ucw-auto` when the level argument is absent) |
| `auto.retry_cap` | int | `3` | max consecutive verify-fail retries at auto-mode level 2+ (consulted by the stop-hook retry loop when `on --retry-cap` is absent) |

Every key above is consulted by its named consumer — none is inert.

Resolution order for reads (first hit wins):

1. `UCW_<KEY>` env var — dots become underscores, uppercased
   (e.g. `kimi.offload` → `UCW_KIMI_OFFLOAD`)
2. `.ucw/state/settings.json` project override
3. registry default

An env var that fails validation for its key's type is ignored and
resolution falls through. Unknown keys are rejected (exit 2).

---

## Subagent delivery & recovery

Applies to every subcommand that spawns subagents (`plan`, `ship`,
`review`, `audit`, `scribe`). The proven recovery ladder:

1. **Prefer file deliverables + tree verification.** Task prompts must name
   a concrete output file (`.ucw/state/verify-report.json`,
   `.ucw/state/review-report.md`, `lane-done` receipts, edited source).
   When an agent returns, verify the TREE — the file exists and is fresh —
   before trusting anything the agent said. The return message is a
   pointer, never the deliverable.
2. **Message-delivered reports get ONE nudge.** If an agent was supposed to
   report back and nothing arrived, send exactly one nudge naming the
   channel: "reply via SendMessage(to: main) with the file path you wrote".
   One nudge, not a conversation.
3. **Then read the agent's transcript.** If the nudge produces nothing,
   read the agent's transcript/output directly — dead agents usually died
   AFTER doing most of the work, and the transcript shows exactly where.
4. **NEVER full-rerun a partially-complete agent.** Verify tree state
   (files written, receipts recorded, tests passing) and finish the
   remaining delta yourself or with a narrowly-scoped follow-up task.
   Re-running from scratch pays the whole cost again and often conflicts
   with the partial work already on disk.

---

## help

Print the dispatch table at the top of this file. For deeper discovery —
listing every CLI helper, subagent, hook, and skill UCW ships — run
`$HOME/.claude/ucw/bin/ucw-help.py [bin|agents|commands|hooks|skills]`.

(Installer plumbing such as `bin/ucw-merge-settings.py` — the deep-merge
helper install.sh uses to splice UCW hooks into an existing Claude Code
`settings.json` — is invoked by install.sh, not dispatched from here.)

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
