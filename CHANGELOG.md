# Changelog

All notable changes to UCW. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely, semver in spirit.

## [Unreleased]

### Added — Kimi K3 cross-model integration (opt-in, advisory-first)

UCW can now pull a second model into the loop through the `claude-kimi`
bridge (`claude-kimi -p "<prompt>"` runs Claude Code headlessly against
Kimi K3). All plumbing goes through `bin/kimi_invoke.py` — subprocess
timeout, bounded retries, lenient JSON extraction, and a never-raise
contract (every failure comes back as `ok=False`). It also has a
`--raw` prose mode (any non-empty stdout is success — no JSON
extraction, no retry burn; the right mode for reviews, summaries, and
opinions) and accepts `-` as the prompt to read it from stdin (so
`git diff | kimi_invoke.py - --raw` just works). Timeout and model
resolve from `UCW_KIMI_TIMEOUT_SECS` / `UCW_KIMI_MODEL` env, then the
project's `kimi.timeout_secs` / `kimi.model` settings, then 300s /
`kimi-k3`; the resolved model is exported to the subprocess as
`KIMI_MODEL`. Auth stays the bridge's problem; no secrets or endpoints
live in UCW.

Four surfaces, every one of them off by default:

- **`/ucw review --with-kimi`** — adds a `kimi-second-opinion` lane
  (`bin/ucw-kimi-opinion.py`): one whole-diff pass run in PARALLEL with
  the 9 reviewer subagents as a 10th fan-out lane. Its findings persist
  via the same `add-finding` path and flow through the normal
  disprove → dedup → gate stages — no special treatment.
- **`/ucw review --disprover-model kimi`** — routes the disprove step
  through `bin/ucw-kimi-disprove.py` so Kimi K3 is the opposing model in
  the cross-audit. The Kimi disprover is read-only (`Read,Grep,Glob` —
  no shell): disproving is reading code, not running it. Default
  unchanged: the haiku **disprover** subagent.
- **`/ucw opinion <question | --diff>`** — ad-hoc second opinion,
  printed VERBATIM under a `## Kimi (advisory, unverified)` banner.
  Advisory only; never merged into the agent's own voice.
- **`[kimi]` implementer offload** — the planner may tag bounded,
  mechanical tasks (boilerplate, docs stubs, test scaffolding — never
  auth/security/core-logic) for offload via `bin/ucw-kimi-implement.py`
  (file edits only, no shell). The tag is inert unless the
  `kimi.offload` setting resolves true, and the resulting diff must pass
  the implementer's own verification plus the normal verifier + reviewer
  gates — the same bar as a Claude-authored diff.

New **`/ucw settings`** command (`bin/ucw-settings.py`): per-project
behavior toggles stored at `.ucw/state/settings.json`. Typed, closed key
registry (`kimi.review`, `kimi.disprover`, `kimi.offload`, `kimi.model`,
`kimi.timeout_secs`, `review.default`, `ship.push`, `ship.pr`,
`scribe.auto`) — settings never store secrets. All nine keys are
consulted by a named consumer wired in `commands/ucw.md` — none is
inert. Resolution precedence: env var (`UCW_KIMI_OFFLOAD`-style) >
project settings file > registry default. Verbs: `list` / `get` /
`set` / `unset`.

Invariant, stated once and enforced everywhere: **Kimi output is never
load-bearing without passing the existing disprove/dedup/gate or
verifier/reviewer machinery, and never runs unattended unless a `kimi.*`
setting explicitly enables it.**

Tests (all mock `subprocess` — zero network, never invoke the real
bridge): `test_kimi_invoke.py`, `test_kimi_review_lane.py`,
`test_kimi_disprove.py`, `test_opinion_command.py`,
`test_kimi_implementer_offload.py`, `test_settings_cli.py`,
`test_settings_command.py`, plus extended `test_review_agents.py`
doc-wiring assertions.

### Fixed — MCP server registered where Claude Code never read it

`install.sh`'s `register_mcp()` wrote the `ucw-memory` server into
`~/.claude/mcp.json`. Claude Code does **not** load that path — user-scope
MCP servers live in `~/.claude.json`. The result: the memory server was a
valid, runnable stdio server that never appeared in `/mcp`, so in-session
`memory.recall` / `memory.note` / `memory.pin` (and `/ucw recall`, `/ucw
pin`) were silently unavailable. Restarting never helped, because the
config was in a file Claude Code doesn't read.

Changes:

- `register_mcp()` now writes the entry to `~/.claude.json` (via
  `$CLAUDE_USER_CONFIG`), the file Claude Code actually loads at user
  scope, merging in place to preserve everything else in that file.
- It also **migrates off** the legacy `~/.claude/mcp.json`: drops our
  entry and deletes the file if no other servers remain.
- `uninstall` strips `ucw-memory` from both the live config and the
  legacy file, and removes the legacy file if it's left empty.
- `verify_install()` checks `~/.claude.json`.
- `bin/ucw-audit.py` now scans `~/.claude.json` (the legacy path stays in
  the scan list so a lingering stale file is still audited).
- `agents/onboarder.md` instructs registering optional Obsidian/Notion
  servers via `claude mcp add` (or `~/.claude.json`), never the dead path.
- Docs (`SECURITY.md`, `memory/README.md`, `agents/security-reviewer.md`)
  updated to point at `~/.claude.json`.

Install/PEP-668 tests updated to assert on `~/.claude.json`.

### Changed — Streak breaker auto-runs verify itself instead of just nagging (PR H)

Completes the trio (PR F: full gate suite at Stop; PR G: streak counts
any gate; PR H: streak hook runs the gate itself).

Before: when `.ucw/state/edit-streak` hit 5, `hooks/post-tool-batch.py`
just blocked with "agent, please run a verify gate." The agent then ran
`bin/ucw-verify.py` (or `tsc`, `eslint`, ...), PostToolBatch fired
again, detected the verify command, and reset the streak. Two extra
turns for a check the hook could have done itself.

PR H makes the hook run the gates itself when the streak hits the
threshold — same pattern as `hooks/stop.py`. Outcomes:

- **All gates pass** → silently reset the streak, agent continues. No
  block, no noise. This works at every auto-mode level — even level 0,
  because running a check isn't an autonomous *action*, it's just doing
  what the agent would have done anyway.
- **A gate fails** → block with the gate name and failure excerpt, so
  the agent fixes the right thing immediately. Streak preserved; the
  fix-and-rerun cycle resets it on the next batch (PR G's broader
  pattern matches the agent's verify run).
- **No runners detected** → reset streak quietly. Don't block when
  there's nothing the agent can run.
- **Verifier crashed / unparseable** → fall back to PR G's
  manual-prompt block ("please run something") so we never leave the
  agent stuck.

**Default gates at streak break: `lint,types` (NOT tests).** Tests are
reserved for Stop because a full vitest/pytest suite can take minutes
— PostToolBatch fires often enough that running tests on every break
would freeze the loop. Typecheck + lint together catch >90% of errors
in seconds. Override with `UCW_STREAK_GATES=lint,types,tests` if you
prefer the slower-but-thorough variant.

New env knobs:
- `UCW_AUTO_STREAK_VERIFY=0` — disable auto-verify, restore PR G's
  "agent must run something" block
- `UCW_STREAK_GATES=tests` — comma-separated subset; same syntax as
  `UCW_VERIFY_GATES` but only affects PostToolBatch

9 new tests in `tests/test_post_tool_batch_detection.py` cover: lint
pass + types pass → silent reset, lint fail → block with summary,
typecheck fail → block with summary, no runners → quiet reset, default
excludes tests, `UCW_STREAK_GATES=tests` opt-in, below-threshold no-op,
disabled-via-env restores old behavior, log includes per-gate status.
1 new schema-compliance test for the auto-verify failure block. Suite:
581 + 1 xfail (was 572).

### Changed — Streak breaker counts ANY verify gate, not just tests (PR G)

Companion to PR F. The PostToolBatch streak breaker
(`hooks/post-tool-batch.py`) was only resetting `.ucw/state/edit-streak`
when it spotted a test-runner invocation (vitest, pytest, jest, ...).
A `pnpm exec eslint .` or `npx tsc --noEmit` run — equally valid
verification — did nothing to the counter. Result: after 5 edits the
breaker would force the agent to run vitest specifically, even when a
typecheck was the more relevant check. Users had to manually run vitest
"to break the streak and continue."

PR G expands `_VERIFY_SIGNAL_PATTERNS` (renamed from
`_TEST_SIGNAL_PATTERNS`) to cover all three gates:

- **Tests**: pytest, vitest, jest, mocha, rspec, playwright, go test,
  cargo test, phpunit (unchanged)
- **Lint**: eslint, biome (check|lint|ci|format), ruff (check|format),
  golangci-lint, cargo clippy
- **Types**: tsc, mypy, cargo check, go vet
- **Generic**: `make <test|lint|typecheck|types|check|verify>` and
  `npm|pnpm|yarn|bun (run|exec)? <test|lint|typecheck|tsc|check|verify|format>`
- **UCW itself**: `bin/ucw-verify.py` invocations (with or without
  `python` prefix) count — that IS verification.

Build/dev scripts (`pnpm run dev`, `pnpm run build`, `npm start`,
`yarn install`) deliberately do NOT count — otherwise the breaker is
useless, the agent could just run `pnpm dev` every few edits and never
verify anything.

User-facing block message updated to name lint/types/tests explicitly
and to suggest `bin/ucw-verify.py` as the one-shot path.

18 new positive shapes + 4 new negative shapes in
`tests/test_post_tool_batch_detection.py` (51 pass + 1 xfail, was 33 +
1 xfail). Full suite: 572 pass + 1 xfail (was 548).

### Changed — Auto-verify now runs the full gate suite (PR F)

Fixes a longstanding mismatch: the `verifier` subagent's design
(`agents/verifier.md:13-21`) calls for a five-gate suite (lint → types →
tests → security → custom), but `bin/ucw-verify.py` — the script the
Stop hook actually calls for auto-verify — was running ONLY the test
runner. A project with passing vitest but broken `tsc --noEmit` or
ESLint would slip through: the hook saw "verify passed", advanced
`phase=verify`, and at auto-mode level 3 immediately nudged `/ucw ship`
to push broken code. Users had to manually re-stop the agent.

PR F implements the first three gates (lint, types, tests). Per gate:
- PREFERENCES.md fields: `linter`, `typechecker`, `test_runner`
- `make lint` / `make typecheck` / `make test` if the target exists
- `package.json` scripts: `lint`, `typecheck` (or `tsc`), `test`, run via
  the detected package manager (pnpm/yarn/bun/npm)
- Stack-detected defaults (eslint/biome/ruff/clippy/golangci, tsc/mypy/
  cargo check/go vet, pytest/vitest/jest/...)
- Else: gate is skipped (transparent — doesn't fail the suite)

Gates run in order, stop at first failure (override with `--all`).
Output JSON adds `gates: [...]` and `failed_gate: "lint"|"types"|"tests"|null`;
top-level `command`/`summary`/`exit_code` point at the failing gate (or
the last passing one) for back-compat with the old single-command shape.

`hooks/stop.py` now names the failing gate in its block message
(`"UCW AUTO (retry 2/3): lint gate \`make lint\` failed..."`) so the
agent doesn't waste a retry hunting failures in the wrong file. Lint
and types failures get the same retry loop as test failures.

New env knobs:
- `UCW_VERIFY_GATES=tests` — comma-separated subset to opt out of
  lint/types if a project intentionally skips them.
- Existing `UCW_VERIFY_TIMEOUT` now applies per-gate (was per-command;
  semantically identical when only one gate ran).

`knowledge/PREFERENCES.md.tmpl` gains `linter` and `typechecker` rows;
`bin/ucw-render-knowledge.py` populates them from detected stack data.

18 new tests in `tests/test_verify.py` and `tests/test_hooks_more.py`
cover: gate detection precedence (prefs > Makefile > package.json >
stack), template-placeholder rejection, stop-at-first-failure, `--all`
mode, `--gates`/`UCW_VERIFY_GATES` subset filtering, lint-failure
blocks Stop, typecheck-failure blocks Stop, all-gates-pass advances
phase. Full suite: 548 + 1 xfail (was 530).

### Changed — SessionEnd preserves phase across exit when mid-workflow (PR E)

Tightens PR D's clear-and-continue support to cover the exit+restart case
too. Previously, `/clear` worked smoothly post-PR-D (no SessionEnd fires)
but exiting Claude Code wiped `.ucw/state/phase` — on restart, the
auto-injected resume hint wouldn't fire (it gates on phase) and the
agent had artifacts but no state-machine position.

PR E changes `hooks/session-end.py::_reset_state()` to **preserve phase**
when both:
- auto-mode is on (`bin/ucw-auto.py`-resolved level > 0), AND
- `.ucw/state/plan.md` exists (work in progress)

Either condition false → original behavior (clear phase, end of work).
`UCW_AUTO_MODE=off` env var still wins as the escape hatch.

`edit-streak` and `auto-retries` continue to clear unconditionally —
streak is per-session momentum, retries are a per-build budget that
should reset.

6 new tests in `tests/test_hooks_more.py` cover:
- phase preserved when auto-on + plan present (the resume case)
- phase still cleared when auto off (back-compat)
- phase still cleared when no plan.md (not really mid-workflow)
- streak + retries still clear even when preserving phase
- preservation is logged for traceability
- env-var off short-circuit still wipes phase

530 tests passing (was 524).

**End user impact.** After PR E, this flow works:

```
# Today
/ucw auto on
/ucw plan add /healthz endpoint
# (build, edit, ...)

# Exit Claude Code, close laptop, come back tomorrow
# Restart Claude Code

# Next prompt:
"continue"
# → UserPromptSubmit fires, sees phase=`build` still set,
#   injects resume hint pointing at plan.md + spec.md
# Agent picks up where it left off.
```

### Added — clear-and-continue support (PR D)

Closes four real gaps the user surfaced when asking "will `/clear` and
`/ucw ship` work?" after the auto-mode series shipped.

**1. Spec persistence.** `agents/planner.md` now writes the scope-phase
output (one paragraph + success criteria + risks) to
`.ucw/state/spec.md` before the approval gate. Previously only the plan
task list survived — commit messages went vague after `/clear`. Now the
agent can re-read the goal from disk.

**2. `auto-retries` leak fixed.** `hooks/session-end.py::_reset_state()`
now also clears `.ucw/state/auto-retries`. PR B introduced this counter
but forgot to wire it into SessionEnd — a counter from yesterday's
failed retry loop would poison today's first failure. Two regression
tests lock the fix (the leak case + verifies `.ucw/state/auto-mode`
itself is correctly preserved as user intent).

**3. Pre-compact digest extended.** `hooks/pre-compact.py` now includes
`auto_mode` level + retry budget, plus pointers to `plan.md` /
`spec.md` if persisted, plus a `Run /ucw resume post-compact …` line.
The digest is now actually consumable instead of being dead data.

**4. Auto-resume injection.** `hooks/user-prompt-submit.py` gets a new
branch that prepends a compact resume hint (~150 chars) when
`.ucw/state/phase` exists. Format:

```
## UCW resume

Workflow state in progress: phase=`build`, auto-mode=L3 (retries 1/3).
Plan/spec at `.ucw/state/plan.md`, `.ucw/state/spec.md`. Run
`/ucw resume` for the full block, or `/ucw status` for the dashboard view.
```

The hint comes first in `additionalContext` so it isn't buried beneath
any matched Knowledge docs. Cost: a few tokens per prompt when workflow
is mid-flight; zero when phase isn't set.

**5. `bin/ucw-resume.py` + `/ucw resume` command.** New helper that
renders a full markdown re-orientation block: phase, auto-mode level +
since + retry budget, persisted spec, persisted plan, HEAD commit +
branch, dirty-tree warning when auto-mode is on AND phase is `land`
(catches "crashed mid-ship" leaving uncommitted WIP), Knowledge file
inventory, and the most recent pre-compact digest. Truncates plan/spec
to configurable byte budgets (default 3 KB / 1.5 KB) so it fits in a
single `additionalContext`.

**Test count.** 524 passing (+ 1 xfail), up from 489 — 35 new tests:

- `tests/test_resume.py` (23) — render output for every field, byte-limit
  truncation, env override precedence, git probes (HEAD / branch /
  dirty), dirty-tree warning gating (auto-on AND phase=land), non-git
  fallback, sub-directory project-root resolution
- `tests/test_hooks_more.py` (5) — auto-retries leak fix, auto-mode
  preservation across SessionEnd, pre-compact digest with auto-mode
  state, digest plan/spec presence markers
- `tests/test_hooks.py` (5) — UserPromptSubmit resume hint injection
  with/without phase, with auto-mode, with retry budget, ordering
  before Knowledge docs, plan/spec pointers

**Behavioral contract.** After PR D:

```
/ucw auto on                              # level 4
/ucw plan add /healthz endpoint           # planner persists spec.md
# (build, edit, ...)
/compact                                  # PreCompact captures auto-mode + retries
"continue"                                # UserPromptSubmit auto-injects resume hint
/ucw resume                               # full block on demand
/ucw ship                                 # works as before — hooks drive from disk
```

End of session: `auto-retries` is cleared. New session: clean retry
budget; `auto-mode` itself preserved.

### Added — auto-mode Level 4: auto-PR + watch CI (PR C of 3 — series complete)

`/ucw auto on` (which defaults to level 4) now drives the entire workflow
end-to-end. After Level 3 finishes the commit + push, `/ucw ship` at
level 4 also:

1. Generates deterministic PR metadata via `bin/ucw-pr-meta.py`
   (title from the commit subject, body from the message + diff stat +
   test plan placeholder)
2. Opens a **draft** PR via `mcp__github__create_pull_request` — draft
   status keeps the human checkpoint cosmetically visible even though
   the work is autonomous
3. Subscribes to PR activity via `mcp__github__subscribe_pr_activity` so
   CI failures and review comments wake the session for autofix —
   bounded fixes auto-applied, ambiguous comments escalate via
   `AskUserQuestion`

**Why a separate helper.** Having the PR title/body come from a Python
helper (instead of the agent improvising every time) makes the output
deterministic, testable, and inspectable: run `bin/ucw-pr-meta.py`
yourself to see exactly what auto-mode would post. 17 tests cover the
title truncation, body sections, diff-stat extraction, branch detection,
detached HEAD fallback, and the "no commits ahead" early-exit (exit 2).

**Helper interface** (`bin/ucw-pr-meta.py [--base main] [--repo .]`):

```json
{
  "title": "<first line of HEAD commit, ≤70 chars, word-boundary safe>",
  "body":  "<markdown with ## Summary / ## Changes / ## Test plan>",
  "head_branch": "<current branch>",
  "base_branch": "main",
  "commit_sha":  "<HEAD sha>",
  "diff_stat":   "<git diff --stat output>",
  "commits_ahead": <int>
}
```

Exit codes: 0 ok / 1 git failure / 2 nothing-to-PR (head == base).

The full series — PRs A through C — ships a working autonomous workflow.
You can now `/ucw auto on`, hand the agent a goal, and walk away: it
plans, builds, retries on failure, commits, pushes, opens a PR, and
watches CI. Disable any time with `/ucw auto off` or `UCW_AUTO_MODE=off`.

### Added — auto-mode Levels 2 + 3 wired into Stop hook (PR B of 3)

Builds on PR A. Both levels are cumulative — turning on level 3 also gets
you level 2's retry loop.

**Level 2 — retry loop on verify failure.** Stop hook reads
`bin/ucw-auto.py level`. When `level >= 2` and verify fails:
- Increment `.ucw/state/auto-retries`
- Block with `AUTO (retry N/cap): <failure summary>` — same effect as the
  old hard block, but the agent now sees a retry budget
- At cap (default 3, configurable via `--retry-cap` or
  `UCW_AUTO_RETRY_CAP`), reset the counter and fall back to a hard block
  with `retry cap exhausted — fix manually` so the human takes over

On verify pass, the retry counter clears.

**Level 3 — auto-commit + push on green.** When `level >= 3` and verify
passes, instead of silently allowing Stop, the hook blocks with
`AUTO (level N): verify passed. Running /ucw ship now …`. The block is the
*carrier* for the instruction — without it the agent would Stop and wait
for the next prompt. `/ucw ship` then reads the same level and skips its
user-confirm gate before committing + pushing.

**Schema compliance.** Both new code paths emit top-level
`decision + reason` only — never `hookSpecificOutput` on Stop. Locked in
by `test_level_*_block_has_no_hookSpecificOutput` cases.

**Drift guard.** `bin/ucw-auto.py` and `hooks/_hook_common.py` each have
their own state-reading code (the CLI vs. self-contained hooks). Five new
tests (`test_hook_helper_matches_bin_*`) assert they agree on every
input — off, on, each level, env override, malformed state.

PR B ships:
- `hooks/_hook_common.py` — `auto_mode_level()` + `auto_retry_cap()` helpers
- `hooks/stop.py` — retry-counter helpers, level 2/3 branches in `main()`
- `agents/`/`commands/ucw.md` — `ship` checks auto level and skips user
  confirm at level 3+
- 21 new tests (16 Stop-hook behavior + 5 drift-guard) — 472 total

PR C lands Level 4: auto-PR creation + `subscribe_pr_activity` for CI
autofix.

### Added — auto-mode foundation + Level 1 plan auto-accept (PR A of 3)

`/ucw auto on [level]` puts UCW into autonomous mode. Levels are cumulative:

- **1** — planner auto-accepts its own spec + plan (no AskUserQuestion gate)
- **2** — Stop hook retry loop on verify failure (lands in PR B)
- **3** — auto-commit + auto-push on green (lands in PR B)
- **4** — auto-open PR + subscribe to PR activity for CI autofix (PR C)

`/ucw auto on` with no number defaults to **level 4**. `/ucw auto off`
clears state. `/ucw auto status` prints the current level + retry cap.

**Escape hatch.** Setting `UCW_AUTO_MODE=off` in the environment
short-circuits everything regardless of state — flip it in one terminal if
the agent goes wrong and you can't get to a file. `UCW_AUTO_MODE=on`,
`UCW_AUTO_MODE=2`, etc. also work.

**State** lives at `.ucw/state/auto-mode` as JSON:
`{"level": N, "since": "<iso>", "retry_cap": <int>}`. Hooks and commands
call `bin/ucw-auto.py level` (prints the integer) rather than parsing the
file directly.

PR A ships:
- `bin/ucw-auto.py` with subcommands `on / off / status / level`
- `/ucw auto …` wiring in `commands/ucw.md`
- Planner agent reads the level and skips its two approval gates at ≥ 1
- 26 tests (`tests/test_auto_mode.py`) — state round-trip, env overrides,
  retry-cap parsing, malformed-state handling

PR B will wire levels 2 and 3 into the Stop hook and `/ucw ship`. PR C
will add level 4 (auto-PR + CI watch).

### Added — PostToolBatch observability (always-on log + opt-in payload capture)

Follow-up to the #8 fix so you can verify in production that the recursive
walker matches what Claude Code actually sends.

- **Always on**: every `PostToolBatch` invocation now writes a
  `detection=<bool> streak=<n> batch_keys=<list>` line to `.ucw/hooks.log`.
  Run `tail -f .ucw/hooks.log` during a build and you'll see whether
  detection fired on each batch.
- **Opt-in raw capture**: setting `UCW_DEBUG_PAYLOADS=1` *or* touching
  `.ucw/state/debug-payloads` makes the hook append the full payload to
  `.ucw/state/post-tool-batch-payloads.jsonl` (one JSON per line).
  Off by default — payloads can include path data. Delete the sentinel
  file or unset the env var to stop capturing.

Use case: if the new walker ever misses a test run in real usage, flip on
capture for one session and the exact payload shape is recorded for the
next round of detection-rule tuning.

4 new tests in `tests/test_post_tool_batch_detection.py` cover the log
line and both capture paths.

### Fixed — PostToolBatch streak breaker now actually detects test runs (#8)

The streak breaker hook (`hooks/post-tool-batch.py`) was supposed to reset
the edit-streak counter when a Bash test invocation appeared in the batch
of completed tool calls. It almost never did, because `_batch_ran_tests()`
hardcoded one specific payload shape — `payload["tools"][i].tool_input.command` —
that didn't match what Claude Code actually emits for `PostToolBatch`. The
result: even right after `pytest -q` succeeded, the streak kept climbing and
the next batch would block with "run tests now."

`PostToolBatch`'s input schema isn't formally documented and has shifted
across Claude Code versions. Rather than chase the moving target, the new
implementation walks the payload tree recursively and matches known test
runner names with **word-boundary regex**: `pytest`, `vitest`, `jest`,
`mocha`, `rspec`, `playwright test`, `go test`, `cargo test`, `make test`,
`npm/pnpm/yarn (run) test`, `bun test`, `phpunit`. Path-like keys
(`file_path`, `cwd`, `transcript_path`, etc.) are skipped to avoid
false-positives from filenames like `tests/test_pytest.py`.

Tests: 25 new cases in `tests/test_post_tool_batch_detection.py` covering
six plausible payload shape variants, twelve test-runner spellings, and
defensive cases (missing keys, non-dict entries, deep nesting, path-key
exclusion). The schema compliance suite (`tests/test_hook_schema_compliance.py`)
also gains two PostToolBatch input-shape tests so PR #6's *output*-schema
work is matched on the input side.

### Changed — Stop hook auto-verifies instead of hard-blocking

When `phase=build` and `streak>0`, the Stop hook used to refuse Stop and tell
the user to run `/ucw ship` manually. That's friction for the common "I want
to pause here, the diff already passes tests" case.

Now Stop **auto-runs** project verification inline:

  - tests **pass** → clear streak, advance phase to `verify`, allow Stop
  - tests **fail** → block with the failing test output in the reason field
  - tests **time out** → block with a hint about `UCW_VERIFY_TIMEOUT`
  - no runner detected → allow Stop (no point blocking when there's nothing
    to verify; configure `test_runner` in PREFERENCES to enable)

The verifier is `bin/ucw-verify.py`, a new helper that:

  - Picks a command in this order: PREFERENCES.md `test_runner` →
    `make test` if Makefile has the target → auto-detect via
    `ucw-detect-stack.py` (pytest, vitest, jest, go test, cargo, mocha, rspec)
  - Defaults to a 60s timeout, overridable via `UCW_VERIFY_TIMEOUT=<sec>`
  - Captures combined stdout+stderr, truncated to 2 KB for the block reason
  - Returns structured JSON: `{passed, skipped?, command, exit_code,
    elapsed_ms, timed_out, summary, source}`
  - Exit codes: 0 pass / 1 fail / 2 timeout / 3 nothing to verify

Escape hatch: `UCW_SKIP_AUTO_VERIFY=1` falls back to the old hard-block
behavior (useful when tests need orchestration the hook can't do, e.g.
`docker-compose up` first).

20 new tests in `tests/test_verify.py` (detection, run paths, timeout,
missing binary, env config) and refreshed Stop tests covering all four
auto-verify outcomes.



### Fixed — hook output schema compliance + UCW state edits don't count toward streak

Two real bugs that user testing exposed during a `/ucw plan` → `/ucw ship` run.

**Bug A — Stop hook validation error.** Claude Code's strict hook output schema
only allows `hookSpecificOutput` on `PreToolUse / UserPromptSubmit / PostToolUse
/ PostToolBatch`. `stop.py` was emitting `hookSpecificOutput` on the `Stop`
event, which the harness rejected with `Invalid input` — even though the
top-level `decision: "block"` was honored. Same issue with `session-start.py`
which used `hookSpecificOutput.additionalContext` for context injection.

Fix:
- `stop.py` — only emits top-level `decision` + `reason`. The hint that used
  to live in `hookSpecificOutput.additionalContext` is now appended to `reason`.
- `session-start.py` — rewritten as a side-effect-only hook. Writes
  `.ucw/state/last-session-start` for the dashboard; emits no JSON. Knowledge
  injection moves to `user-prompt-submit.py` (which IS allowed
  `additionalContext`) and `/ucw status`.

**Bug B — planner's state writes blocked Stop.** `post-tool-use.py`
incremented the streak counter for every file edit inside the project, which
included UCW writing its own state (`.ucw/state/plan.md`, `.ucw/reviews/...`,
log files). After `/ucw plan`, streak was 1, phase was `build`, and `stop.py`
refused to let the session end ("cannot Stop in Build phase with 1 edits not
verified") — even when no actual code had changed.

Fix: `post-tool-use.py` skips files under `.ucw/state/`, `.ucw/reviews/`,
`.ucw/hooks.log`, `.ucw/sessions.log`, `.ucw/subagents.log`. They get logged
("ignored UCW state write") but don't bump the streak or trigger syntax checks.

**12 new tests** in `tests/test_hook_schema_compliance.py`:

- One per hook (Stop, SessionStart, SubagentStop, PreCompact, SessionEnd,
  PreToolUse, PostToolUse, PostToolBatch, UserPromptSubmit) asserting the
  output (under realistic block/inject conditions) passes a strict schema
  validator
- `test_planner_writing_state_does_not_increment_streak`
- `test_review_findings_log_does_not_increment_streak`
- `test_real_code_edit_does_increment_streak` (sanity check the negative case)

`tests/test_hooks.py::test_session_start_*` and
`tests/test_hooks_more.py::test_stop_does_not_emit_hookSpecificOutput`
updated to lock in the new shapes.

376/376 passing. ruff clean, shellcheck (0.9 + 0.11) clean, audit clean,
smoke green.

### Fixed — install.sh actually installs commands, agents, and skills

The previous installer copied rules + hooks + bin but **never linked the
slash commands, subagents, or skills** into Claude Code's discovery dirs.
`/ucw` wasn't a command, `reviewer-injection` couldn't be invoked as a
subagent, and the shipped skills were undiscoverable.

Added three install actions:
- `install_commands` symlinks `commands/*.md` → `~/.claude/commands/`
- `install_agents` flat-links every `.md` under `agents/` (incl.
  `agents/reviewers/*`) → `~/.claude/agents/` (Claude Code uses the
  `name:` frontmatter, not filename, so flat-linking by basename works)
- `install_skills` symlinks each SKILL.md's parent dir →
  `~/.claude/skills/<name>/` so adjacent assets travel with it

Uninstall (`_remove_ucw_symlinks_in`) walks each dir and removes only
symlinks whose `readlink` target lives under `$REPO_ROOT` — user-authored
commands/agents/skills are left alone. 7 regression tests lock this in.

### Fixed — install.sh respects PEP 668 (Debian / Ubuntu / Zorin / Homebrew 3.11+)

The previous installer ran `pip install -e ./memory` against the system
Python and fell back to "set PYTHONPATH" when that failed. On PEP 668
systems (any modern Debian-family distro, Homebrew Python 3.11+) pip
refuses to install into the system environment with `externally-managed-environment`,
so the package was never installed. The MCP server's `command: python3` in
mcp.json then couldn't import `ucw_memory` and Claude Code couldn't reach
the memory tools.

**Fix:**
- `install.sh` now creates a dedicated venv at `$UCW_HOME/venv` whenever:
  - `uv` is available (uv handles PEP 668 transparently), OR
  - the system Python has the EXTERNALLY-MANAGED marker file
- `ucw-memory` is installed into that venv (`-e ./memory`)
- `mcp.json`'s `command` is now the **absolute path** to the venv's python
  (`$UCW_HOME/venv/bin/python`), not bare `python3`
- New flag `--reinstall-deps` blows away `$UCW_HOME/venv` and rebuilds
- `--uninstall` now removes the venv along with hooks/bin/lib
- The mcp.json template (`mcp/ucw-memory.json`) carries a `_comment`
  documenting that install.sh generates the entry; don't hand-copy it

7 regression tests in `tests/test_install_pep668.py` lock the invariants:
venv exists, venv's python can import ucw_memory, mcp.json's command is
the absolute venv path (not "python3"), reinstall reuses by default,
`--reinstall-deps` rebuilds, `--uninstall` removes the venv,
`--dry-run` creates nothing.

### Changed — single `/ucw` umbrella (no built-in collisions)

Claude Code reserves `/plan`, `/init`, and `/review` as built-in slash
commands. All UCW commands are now subcommands of `/ucw` so there's
exactly one root and zero collisions:

  /ucw status | init | plan | ship | review | audit | recall | pin |
       scribe | distill | dashboard | watch | unwatch | worktree |
       phase | prefs | help

The 10 standalone `commands/*.md` files were deleted and their behavior
folded into `commands/ucw.md` as dispatch branches. A new test enforces
the rule: any new file in `commands/` with a Claude-built-in name fails
CI.

### Added — `/ucw review` (cross-audit orchestrator, Cloudflare pattern)

- **`/ucw review`** and **`/ucw review --full`** — fan out to 9 narrow-scope
  reviewers in parallel, then a different model (haiku) tries to **refute**
  each finding (cross-audit), then reachability is determined separately
  for security findings (chain split). Effective severity comes from
  deterministic code, not any single agent.
- `bin/ucw-review.py` — orchestrator CLI: `scope`, `add-finding`, `disprove`,
  `reachability`, `dedup`, `gate`, `approve`, `status`, `summary`, `list`.
- `memory/ucw_memory/findings.py` — schema, append-only JSONL store under
  `.ucw/reviews/<sha>/`, `_effective_severity()` derived from raw severity
  + disprover verdict + reachability verdict (refuted → drop 2 levels;
  unreachable security → drop 1 level).
- 9 narrow-scope reviewer agents under `agents/reviewers/`: correctness,
  injection, deserialization, auth, performance, data-loss, api-compat,
  tests, docs. Each says "only my concern" — they explicitly will not flag
  outside their scope.
- `agents/disprover.md` — haiku model, **cannot generate new findings**;
  job is to refute or confirm.
- `agents/reachability.md` — sonnet, traces from external entry points to
  the bug site; verdicts: reachable / unreachable / unclear.
- `commands/ucw.md` (review subcommand) — orchestration script; `--quick` for single-pass,
  `--narrow <area>` to scope, `--since <ref>` for explicit base.
- Governance lives outside the model: approve/audit-trail/gate are all
  deterministic and persisted; the model can't bypass them.

### Polish pass (prior)
- Every CLI has `--help`, structured JSON output, meaningful exit codes
- `ruff.toml` config + `ruff check` integrated into CI
- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`
- `dashboard/statusline.sh` — Claude Code statusLine showing phase, streak, memory, stale-doc count
- `bin/ucw-distill-instincts.py` — promotes recurring fact patterns into Skill drafts
- `bin/ucw-knowledge-check.py` — flags stale Knowledge docs (used by `/ucw status` and CI)
- `bin/ucw-worktree.py` — create/list/cleanup/remove for parallel agent fan-out
- Color output in `dashboard/cli.py status` (TTY-detected, `--no-color` to disable)
- Markdown output mode in `ucw-audit.py` (`--markdown` for PR comments)
- Install.sh: `--dry-run`, `--verbose`, post-install `verify_install` check,
  symlinks for hooks/bin so `git pull` updates installed versions
- `Fact` dataclass now carries `source_session` so instinct aggregation can
  count distinct-session occurrences
- 21 new tests bringing the count to 136

### Fixed
- Worktree `remove`/`cleanup` no longer pass empty-string args to git
- Knowledge renderer dict had `deploy_target` duplicated; consolidated to the
  STACK-section line which handles both detection fallback and preference override
- Ruff cleanup: zip(strict=), proper subprocess args, kw-only regex flags

## [0.1.0] — initial scaffold (2026-05)

### Added
- Marketplace bundle (`plugin.json`, `marketplace.json`) with 7 sub-plugins
- `install.sh` with minimal/standard/full profiles, `--uninstall`, `--post-marketplace`
- Settings fragments for each profile, registering 9 hook event handlers
- 8 subagent definitions: planner, implementer, verifier, reviewer,
  security-reviewer, memory-curator, scribe, onboarder
- 10 slash commands (now consolidated under `/ucw`): `/ucw plan`, `/ucw ship`, `/ucw recall`, `/ucw pin`,
  `/ucw scribe`, `/ucw distill`, `/ucw audit`, `/ucw dashboard`
- 7 Knowledge templates: INDEX, STACK, DESIGN, CONVENTIONS, GLOSSARY,
  ROADMAP, PREFERENCES
- Memory subsystem: SQLite + FTS5 + sqlite-vec-ready schema, real MCP server
  over stdio JSON-RPC, hybrid retrieval (BM25 + recency + optional rerank),
  contextual-retrieval pipeline (Anthropic pattern), Claude-Haiku reranker
  for the no-Voyage-key path
- 9 hooks: session-start/end, user-prompt-submit, pre/post-tool-use,
  post-tool-batch (streak breaker), stop, pre-compact, subagent-stop
- Distillation pipeline with privacy stripping and bare-conclusion gate
- Phase tracker (`bin/ucw-phase.py`) wired into `/ucw plan` and `/ucw ship`
- Security audit scanner (`bin/ucw-audit.py`) with 14 secret families,
  5 injection patterns, MCP shell-exec detection, reviewer-write detection,
  `audit-allow:` suppression mechanism
- End-to-end smoke test (`scripts/smoke.sh`) and `Makefile` (`test`, `smoke`,
  `audit`, `lint`, `validate`)
