# Changelog

All notable changes to UCW. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely, semver in spirit.

## [Unreleased]

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
