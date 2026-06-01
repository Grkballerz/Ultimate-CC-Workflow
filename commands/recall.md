---
description: Hybrid memory search — BM25 + vector + rerank. Surfaces prior decisions, facts, and instincts relevant to the query.
argument-hint: "<query>"
---

Call `mcp__ucw-memory__memory.recall` with:
- `query`: $ARGUMENTS
- `k`: 12
- `budget_chars`: 4000

Display results as a numbered list with source session, age, and confidence. Inject the top-k into context for follow-up turns.
