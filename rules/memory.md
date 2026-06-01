# Memory rules

How to use the UCW memory MCP server effectively.

## When to recall

- Before declaring "we decided X" → check `memory.recall("X")`
- Before re-implementing something that feels familiar → `memory.recall("<feature name>")`
- When a goal mentions a person, tool, or convention by name → recall it

## When to note

- A non-obvious decision the user just made ("we don't use barrel exports here, because…")
- A workaround for a specific bug or library quirk
- A user-stated preference that didn't fit any PREFERENCES category

`memory.note` always requires a `reason`. Facts without reasons are dropped by the quality gate.

## Privacy

- `<secret>...</secret>` — never written to disk
- `<private>...</private>` — kept in-session only; stripped from distillation
- `<no-memory>...</no-memory>` — synonym for `<private>` for readability

Default behavior is to keep things; opt out explicitly when needed.

## Pinning

- `memory.pin(fact)` for project-critical facts that must be in every SessionStart
- `memory.pin(fact, scope=global)` for personal preferences that apply everywhere
- Pinned facts never expire and always make the budget cut
