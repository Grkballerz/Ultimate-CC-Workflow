---
name: tdd-loop
description: The canonical RED-GREEN-REFACTOR loop. Activate when adding a new feature with non-trivial behavior, fixing a bug with a reliable repro, or working in a stack where the test runner is fast.
when_to_use:
  - New feature with non-trivial behavior
  - Bug fix where you have (or can write) a failing test
  - Working in a fast-test stack (vitest, pytest, go test)
when_not_to_use:
  - Pure typo / formatting fix
  - Refactor with no semantic change (use test-pinning instead)
  - Spike / exploratory code
---

# TDD Loop

Three movements: **Red, Green, Refactor**. The mistake most agents make is
collapsing them into "write code + tests in the same Edit". Keep them separate.

## Red — write a test that fails

1. Identify the smallest observable behavior the change should produce
2. Add a single test that asserts that behavior
3. Run it. **Confirm it fails for the right reason** (not import error, not
   syntax error — the assertion fired)

> If the test passes immediately, either the behavior already exists (back
> out the test) or the test isn't actually testing what you think.

## Green — make it pass minimally

1. Make the smallest change that flips the test green
2. Don't refactor. Don't add adjacent improvements. Don't optimize.
3. Run the test. Confirm green.

> If you find yourself making 2+ unrelated changes to get green, the
> test was too coarse. Back out, narrow the test, try again.

## Refactor — improve the design

1. Now (and only now) restructure for clarity, dedup, naming, etc.
2. Re-run all tests after each refactor step
3. Stop when the code is clean, not "clean-ish — let me also …"

## After all three

- Commit. The diff should be small and self-contained.
- The post-tool-batch hook's streak counter resets on test invocation.

## Anti-patterns

- "I'll add the test after." → test gets skipped under deadline pressure
- "Multiple tests at once." → blurs RED step; you don't know which test
  caused the failure
- "Refactor while still RED." → makes diagnosis impossible
- "100% test coverage." → not the goal; behavioral coverage is

## Stack-specific notes

- **Python (pytest)**: `pytest -x -k name` runs to first failure on the
  named test
- **TypeScript (vitest)**: `vitest run --watch=false path/to/test`
- **Go**: `go test -run TestX ./pkg/...`
- **Rust**: `cargo test --test integration name -- --exact`
