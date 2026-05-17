# UCW Core Rules

These rules apply to every Claude Code session in a UCW-installed environment.

## Workflow

- For non-trivial multi-file work, go through the 5 phases: Scope → Plan → Build → Verify → Land. Trivial single-file edits may skip Scope+Plan.
- Phase boundaries are blocking. Do not advance to Build without a user-approved plan. Do not Land without all gates passing and zero critical review findings.
- The plan lives at `.ucw/state/plan.md`. Tick tasks as you complete them.

## Knowledge

- Read `.ucw/knowledge/INDEX.md` at the start of every session. Read full docs lazily as relevant.
- Never edit `.ucw/knowledge/*` directly during Build — that's the scribe's job after Land.
- If you notice STACK / DESIGN / CONVENTIONS is wrong, surface it for the scribe; don't silently fix it inline.

## Memory

- Use `memory.recall(<query>)` before assuming you remember a prior decision.
- Use `memory.note(<fact>, <reason>)` for explicit facts worth keeping (the auto-distiller will catch most; use `note` for things you'd otherwise re-explain).
- Wrap sensitive content in `<private>…</private>`, `<no-memory>…</no-memory>`, or `<secret>…</secret>` to keep it out of the distillate.

## Gates

- After 5 edits without a test run, the streak-breaker hook will refuse Stop. Run the relevant test or verifier before continuing.
- If a gate fails, fix it in the same Build phase. Don't move on to the next plan task.

## Tools & permissions

- Never `--no-verify` or `--no-gpg-sign` unless the user has explicitly asked.
- Never `git push --force` to main/master. To force-push a feature branch, confirm first.
- Never edit `.ucw/` files outside your agent's scope.
