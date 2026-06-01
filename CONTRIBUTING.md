# Contributing to UCW

Thanks for considering a contribution. UCW is opinionated by design — proposals
that move us toward "less ceremony, more leverage" are most likely to land.

## Quick start

```bash
git clone <repo>
cd ultimate-cc-workflow
make install-dev       # editable install of the memory package
make validate          # tests + lint + audit (everything CI runs)
make smoke             # end-to-end demo against a synthetic repo
```

## How the codebase fits together

| Area | Where | Status |
|---|---|---|
| Marketplace bundle | `.claude-plugin/{plugin,marketplace}.json` | ✅ |
| Installer | `install.sh` | ✅ |
| Settings fragments | `settings/{minimal,standard,full}.json` | ✅ |
| Agents | `agents/*.md` | ✅ 8 core + lang stubs |
| Slash commands | `commands/*.md` | ✅ 10 |
| Knowledge templates | `knowledge/*.md.tmpl` | ✅ 7 |
| Skills | `skills/<area>/<name>/` | 🚧 promoted via `/distill` |
| Hooks | `hooks/*.py` | ✅ 9 |
| Memory subsystem | `memory/ucw_memory/` (installable pkg) | ✅ |
| CLI helpers | `bin/ucw-*.py` | ✅ 8 |
| Dashboard | `dashboard/cli.py` + `statusline.sh` | ✅ |
| Tests | `tests/` (pytest) | ✅ 136 |

## Standards

- **Tests are not optional.** Anything new (CLI, hook, memory function) ships
  with tests. Aim for unit + at least one integration-style test that exercises
  the user-visible behavior.
- **Ruff clean.** Run `ruff check .` before pushing. Config is in `ruff.toml`.
- **No new dependencies without justification.** The FTS5 path is stdlib-only
  on purpose. Optional features (Voyage, sqlite-vec, anthropic SDK) go under
  `pyproject.toml` extras.
- **Hooks must never crash.** They run on every event; uncaught exceptions
  hurt the user. Wrap risky code, log to `.ucw/hooks.log`, return 0.
- **CLI helpers have `--help`, `--json` (where output is structured), and
  meaningful exit codes.** Audit/lint/check tools should return non-zero on
  problems, 0 on success.

## Local feedback loop

```
make test           # pytest only (~2s)
make smoke          # demo end-to-end (~3s)
make audit          # security scanner on this repo
make validate       # everything CI runs
```

If you touch `install.sh`, also run `bash -n install.sh` and (if you have it)
`shellcheck install.sh`.

## Submitting

1. Branch from `main`. UCW uses `claude/<feature>-<slug>` for AI-driven work
   and `feat/<slug>` for human-authored PRs.
2. Keep commits focused — one logical change per commit, with a body that
   explains the *why*.
3. Run `make validate`. Push. Open a PR.
4. CI runs shellcheck + JSON validation + pytest + ruff + audit. All must
   pass before merge.

## Design principles

- **Knowledge is the canonical truth. Memory is the long tail.** Don't fight
  this: features that blur the line should be discussed first.
- **Hooks enforce policy; they don't dictate workflow.** A hook may *block*
  bad outcomes; it must never *demand* a specific approach inside an
  unblocked path.
- **Reranker is pluggable.** Don't hardcode Voyage or Claude — go through
  `rerank.make_reranker()`.
- **Privacy is default-on.** Anything written to disk goes through
  `strip_private()` first. New sinks must do the same.

## Reporting issues

Open a GitHub issue. Include:
- UCW version (`grep '"version"' .claude-plugin/plugin.json`)
- Python version + OS
- `make validate` output if it relates to a test failure
- `python3 bin/ucw-audit.py --repo .` output if security-related
