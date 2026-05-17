# Changelog

All notable changes to UCW. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely, semver in spirit.

## [Unreleased]

### Added
- Polish pass: every CLI has `--help`, structured JSON output, meaningful exit codes
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
