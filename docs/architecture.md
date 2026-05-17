# UCW Architecture

This document is the durable reference for UCW's design. For operational
guidance see [`workflow.md`](workflow.md).

---

## System diagram

```
                                ┌─────────────────────────────────────────┐
                                │             Claude Code session          │
                                │                                          │
  ┌─ user types ─►  /plan ──────┼──► planner agent ──► .ucw/state/phase   │
  │                /ship        │     implementer       .ucw/state/plan.md│
  │                /recall      │     verifier          .ucw/state/streak │
  │                /audit       │     reviewer                             │
  │                                  scribe                                │
  │                                                                        │
  │   Knowledge ◄──── scribe ◄──── Land ──┐                                │
  │   .ucw/knowledge/*.md                  │                                │
  │     INDEX  STACK  DESIGN ...           │                                │
  │                                        │                                │
  │   Memory ◄──── distill ◄──── SessionEnd│                                │
  │   .ucw/memory.sqlite                   │                                │
  │     facts + FTS5 + embeddings          │                                │
  │     instincts + sessions               │                                │
  │                                        │                                │
  │                                        ▼                                │
  │   Hooks ──────────────────────► hooks/                                  │
  │     SessionStart  PreToolUse                                            │
  │     PostToolUse   PostToolBatch (streak-breaker)                        │
  │     Stop (phase-aware)  PreCompact                                      │
  │     SessionEnd (runs distiller)                                         │
  │                                                                        │
  │   MCP   ucw-memory  (stdio JSON-RPC, 7 tools)                          │
  │         obsidian / notion (optional, mirrors Knowledge)                │
  │                                                                        │
  │   Bin   ucw-audit         ucw-distill-instincts                        │
  │         ucw-detect-stack  ucw-knowledge-{check,diff}                   │
  │         ucw-phase         ucw-render-knowledge                         │
  │         ucw-worktree                                                   │
  └─────────────────────────────────────────────────────────────────────────┘
```

The flow: **session starts** → SessionStart hook injects Knowledge INDEX +
recalled memory → **work happens** through phases (Scope → Plan → Build →
Verify → Land), each gated by hooks → **session ends** → SessionEnd hook
distills facts back into memory → **next session** picks them up.

---

## Six Pillars

| Pillar | Job |
|---|---|
| **Knowledge** | Living human-readable repo docs (`.ucw/knowledge/*.md`) — what *this* repo *is* |
| **Memory** | Durable, queryable cross-session facts (SQLite + sqlite-vec) — what *has happened* |
| **Workflow** | Scope → Plan → Build → Verify → Land, with blocking gates |
| **Orchestration** | Parallel + sequential subagent chains, worktree isolation |
| **Learning** | Distill session wins into instincts; promote instincts to skills |
| **Observability** | Token budget, gate pass rate, memory ROI, stale-doc detection |

**Knowledge vs Memory.** Knowledge is canonical, editable, committable, and small. Memory is the long tail — granular facts that benefit from hybrid retrieval. The scribe keeps Knowledge in sync with reality; the distiller keeps Memory in sync with sessions.

---

## Memory Subsystem

**Storage.** Two-scope SQLite + `sqlite-vec`.
- Global: `~/.claude/ucw/memory.sqlite`
- Project: `<project>/.ucw/memory.sqlite`

**Embedding stack (Anthropic-native contextual retrieval).**
1. Claude Haiku 4.5 generates a one-sentence contextual prefix per fact
2. Voyage `voyage-3` (or `voyage-code-3`) embeds `prefix + fact` to 1024-dim
3. FTS5 indexes the raw fact

**Retrieval.**
1. Hybrid BM25 + vector ANN
2. Reciprocal Rank Fusion merges rankings
3. Voyage `rerank-2` reranks top ~30 to top 12
4. Pinned facts always included first; rest by score under char budget

**Fallback.** No Voyage key → pure FTS5 + Claude Haiku reranking. Slower, slightly less precise, works with Anthropic key alone.

**MCP tools exposed.** `memory.recall`, `memory.pin`, `memory.forget`, `memory.note`, `memory.stats`, `memory.list`, `memory.merge`, `memory.expire`, `memory.init`.

---

## Workflow — 5 Phases

| Phase | Owner | Output | Blocking gate |
|---|---|---|---|
| 1. Scope | planner | one-paragraph spec, success criteria | spec approved |
| 2. Plan | planner | task list with file paths, parallel groups | plan approved |
| 3. Build | implementer (+ lang expert) | code changes | per-file PostToolUse gates |
| 4. Verify | verifier | gate report | all gates pass |
| 5. Land | implementer → scribe | commit, push, PR, Knowledge refresh | clean git status |

Free within phases. Blocking between them.

---

## Hooks & Feedback Loops

Three concentric loops.

**Inner (per tool call).** `post-tool-use.py` lints/typechecks edited files. `pre-tool-use.py` blocks destructive Bash patterns.

**Mid (per turn).** `post-tool-batch.py` is the streak breaker (>5 edits without a test run). `stop.py` blocks Stop in Build phase if gates aren't green.

**Outer (cross-session).** `session-start.py` injects `INDEX.md` + retrieved facts + open work-item. `session-end.py` distills the transcript into memory. `pre-compact.py` writes a digest so post-compact retains state.

**PR loop.** `/ucw watch <PR>` subscribes to GitHub PR activity; CI failures auto-trigger a bounded re-plan or escalate.

---

## Subagents

| Agent | Model | Role |
|---|---|---|
| planner | sonnet | Scope + plan, no edits |
| implementer | sonnet | Build phase, owns edits |
| verifier | haiku | Runs gates, read-only |
| reviewer | opus | Two-stage review: spec then quality |
| security-reviewer | opus | Adversarial: attacker → defender → auditor |
| memory-curator | haiku | Dedupe, prune, score |
| scribe | sonnet | Keeps Knowledge in sync |
| onboarder | sonnet | `/ucw init` wizard |
| {lang}-expert | sonnet | Idiomatic patterns per language |
| orchestrator | sonnet | Decomposes work, fans out worktrees |

---

## Distribution

- **Path A** — Marketplace plugin via `.claude-plugin/marketplace.json`. Sub-plugins allow à la carte install (`ucw-core` is required; orchestration / security / lang packs are opt-in).
- **Path B** — `install.sh` for rules + hooks the marketplace can't ship. Idempotent. Profiles: `minimal`, `standard`, `full`.

---

## Resolved Defaults

- Embedding model: Voyage (`voyage-3` / `voyage-code-3` / `rerank-2`) + Claude Haiku for the contextual-prefix step. Claude-Haiku-only fallback for users without a Voyage key.
- Knowledge backing store: local `.ucw/knowledge/*.md` is always the source of truth; Obsidian and Notion are opt-in mirrors.
- Scribe aggressiveness: auto-apply additive edits, pause for structural rewrites (tunable in PREFERENCES).
- Cross-project memory promotion: `/pin --global` available from v1.
- Onboarding ask budget: 6 questions up front (image, diagrams, deploy, db, package manager, docs surface); JIT for the rest.
