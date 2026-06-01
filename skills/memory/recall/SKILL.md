---
name: memory-recall
description: Query UCW memory before acting on assumptions. Activate when a goal references a prior decision (`"the auth flow"`, `"the database we picked"`), when you're about to re-implement something that feels familiar, or before re-explaining a project convention.
when_to_use:
  - Goal references a prior decision by name
  - Feature feels like a near-duplicate of past work
  - About to re-state "we decided X" — verify first
when_not_to_use:
  - Brand-new project with no memory yet
  - Trivial / one-off operation
---

# Memory Recall

Memory is the long tail of what's been decided / discovered in this project,
queryable via the `ucw-memory` MCP server with hybrid retrieval.

## How to call

```
mcp__ucw-memory__memory.recall(query="<phrase>", k=12, budget_chars=4000)
```

Arguments:
- `query` — free text; ideally the noun phrase you're about to act on
- `k` — top-k hits (default 12)
- `budget_chars` — total context size for injected facts (default 4000)
- `scope` — `project` | `global` | `all` (default `all`)

## What you get back

```json
{
  "embedding_mode": "fts+rerank-claude",
  "hits": [
    {"id": 17, "score": 1.8, "text": "we use postgres (because JSONB)",
     "scope": "project", "pinned": false, "confidence": 0.7},
    ...
  ]
}
```

Hit score combines BM25, recency decay (14-day half-life), and (when
configured) Claude or Voyage rerank.

## Pinned facts

Anything pinned (`memory.pin(fact_id)`) always appears regardless of relevance.
Pin sparingly — too many pins crowd out retrieval signal.

## When recall misses

- Try synonyms — FTS is keyword-based when no reranker is active
- Check the embedding mode in `/ucw status` — if it's `fts-only`, set
  `ANTHROPIC_API_KEY` for Claude reranking
- Walk newer with `memory.list(scope="project", limit=20)` to see what's
  there

## Anti-patterns

- **Recalling on every turn.** Inject once at session start (the
  `session-start.py` hook does this) and on prompts that mention prior work.
- **Treating memory as ground truth.** Memory contains decisions and
  conventions, not invariants. Verify against code / Knowledge docs.
- **Forgetting to `memory.note()` when you make a new decision.** The
  distiller will catch obvious cases, but explicit notes are higher-quality.
