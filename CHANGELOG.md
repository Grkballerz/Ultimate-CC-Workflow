# Changelog

All notable changes to UCW. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely, semver in spirit.

## [Unreleased]

### Added — `/review` (cross-audit orchestrator, Cloudflare pattern)

- **`/review`** and **`/review --full`** — fan out to 9 narrow-scope reviewers
  in parallel, then a different model (haiku) tries to **refute** each
  finding (cross-audit), then reachability is determined separately for
  security findings (chain split). Effective severity comes from
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
- `commands/review.md` — orchestration script; `--quick` for single-pass,
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
- 10 slash commands: `/ucw`, `/init`, `/plan`, `/ship`, `/recall`, `/pin`,
  `/scribe`, `/distill`, `/audit`, `/dashboard`
- 7 Knowledge templates: INDEX, STACK, DESIGN, CONVENTIONS, GLOSSARY,
  ROADMAP, PREFERENCES
- Memory subsystem: SQLite + FTS5 + sqlite-vec-ready schema, real MCP server
  over stdio JSON-RPC, hybrid retrieval (BM25 + recency + optional rerank),
  contextual-retrieval pipeline (Anthropic pattern), Claude-Haiku reranker
  for the no-Voyage-key path
- 9 hooks: session-start/end, user-prompt-submit, pre/post-tool-use,
  post-tool-batch (streak breaker), stop, pre-compact, subagent-stop
- Distillation pipeline with privacy stripping and bare-conclusion gate
- Phase tracker (`bin/ucw-phase.py`) wired into `/plan` and `/ship`
- Security audit scanner (`bin/ucw-audit.py`) with 14 secret families,
  5 injection patterns, MCP shell-exec detection, reviewer-write detection,
  `audit-allow:` suppression mechanism
- End-to-end smoke test (`scripts/smoke.sh`) and `Makefile` (`test`, `smoke`,
  `audit`, `lint`, `validate`)
