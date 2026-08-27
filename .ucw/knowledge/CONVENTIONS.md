# Conventions

> How we write code in this project. Auto-seeded from config files; refined by
> the scribe as patterns emerge in review.

## Naming
- _to be filled in as patterns emerge_

## File / directory layout rules
- _to be filled in as patterns emerge_

## Test patterns
- use `pytest` (auto-detected)
- for anything that shells out to an external model (e.g. `claude-kimi`),
  mock `subprocess.run` and assert zero network calls — never invoke the
  real binary in tests. Cover timeout, nonzero exit, and malformed-output
  paths explicitly, not just the happy path (see `tests/test_kimi_invoke.py`).
- `commands/ucw.md`'s dispatch table is the source of truth for `/ucw`
  subcommands — never hardcode a subcommand list in a test. Parametrize
  against the doc instead (see `tests/test_dispatch_drift.py`), so the
  table and the tests can't silently drift apart.

## Error handling
- external-model bridges (Kimi and any future vendor integration) follow a
  never-raise, graceful-degradation contract: on timeout, nonzero exit, or
  malformed output, warn and return zero findings / an empty result — exit
  0, don't crash the caller, and never fabricate a verdict or finding to
  fill the gap.
- gates distinguish setup-skip (missing tool, transparent pass) from
  strict failure: outside verify/land phase and below auto-level 2, a
  missing tool is a setup-skip; in verify/land phase or at auto-level >= 2,
  the same missing tool is a gate FAILURE — nobody is left in the loop to
  notice a silently skipped gate (see `bin/ucw-verify.py: strict_gates`).
- transport fallbacks between external-model paths must be conservative —
  only for tool-less calls, never silently crossing into a weaker
  permission model.

## Logging
- _to be filled in_

## Imports / module boundaries
- _to be filled in_

## Comments policy
- default to no comments; add only when WHY is non-obvious

## Commit message style
- _to be filled in — scribe will infer from recent commits_
