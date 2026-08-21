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

## Error handling
- external-model bridges (Kimi and any future vendor integration) follow a
  never-raise, graceful-degradation contract: on timeout, nonzero exit, or
  malformed output, warn and return zero findings / an empty result — exit
  0, don't crash the caller, and never fabricate a verdict or finding to
  fill the gap.

## Logging
- _to be filled in_

## Imports / module boundaries
- _to be filled in_

## Comments policy
- default to no comments; add only when WHY is non-obvious

## Commit message style
- _to be filled in — scribe will infer from recent commits_
