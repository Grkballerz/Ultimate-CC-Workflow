# Ultimate Claude Code Workflow (UCW)

An opinionated, end-to-end workflow framework for Claude Code that combines the
best ideas from Superpowers, Everything Claude Code, wshobson/agents,
claude_memory, and the broader ecosystem into a single coherent system.

> **Status:** working end-to-end. All milestones M1–M6 shipped; 700+ tests passing; smoke test exercises the full pipeline.
> Run `make smoke` to see it lay down a fake repo, render knowledge docs, distill facts, recall them via MCP, and audit — all in ~3s.
> See [docs/architecture.md](docs/architecture.md) and the build plan for what's next.

---

## What it gives you

| Pillar | What it does |
|---|---|
| **Knowledge** | Living, human-readable repo docs (`STACK.md`, `DESIGN.md`, `PREFERENCES.md`, …) checked into git and auto-maintained by a **scribe** subagent. Optional Obsidian / Notion mirrors. |
| **Memory** | Cross-session SQLite + `sqlite-vec` store using Anthropic's contextual-retrieval pattern (Voyage embeddings + Claude Haiku contextual prefixes + RRF + reranking). Claude-only fallback when no Voyage key. |
| **Workflow** | 5-phase Scope → Plan → Build → Verify → Land with blocking gates between phases and full autonomy within them. |
| **Orchestration** | Multi-agent subagent chains, worktree-isolated parallelism, model tiering (Opus / Sonnet / Haiku). |
| **Learning** | Session transcripts → distilled facts → promoted instincts → reusable skills. |
| **Observability** | Token budget, gate pass rate, memory ROI, stale-doc detection via `/ucw status` (CLI v1) and Flask dashboard (M6+). |

Cross-cutting layers: **Hooks** (policy), **Skills** (knowledge), **Subagents** (scoped experts), **MCP** (external tools), **Rules** (always-on guidance), **Commands** (orchestrators).

---

## Install

### Path A — Claude Code marketplace (recommended)

```
/plugin marketplace add <owner>/ultimate-cc-workflow
/plugin install ucw-core@ucw
```

Then run `./install.sh --profile standard --post-marketplace` once to lay down
rules and hooks that the marketplace can't ship.

### Path B — Local install script

```
git clone <repo>
cd ultimate-cc-workflow
./install.sh --profile {minimal|standard|full}
```

| Profile | Includes |
|---|---|
| `minimal` | memory MCP + core rules |
| `standard` | + workflow agents, verification gates, inner-loop hooks |
| `full` | + security-reviewer, all language packs, dashboard, PR-watch |

The installer is idempotent. Run `./install.sh --uninstall` to reverse.

---

## First-session experience

On your first Claude Code session after install, UCW auto-suggests `/ucw init`,
which:

1. Detects your stack from `package.json`, `pyproject.toml`, `go.mod`, etc.
2. Drafts `.ucw/knowledge/STACK.md` and `CONVENTIONS.md`
3. Asks 6 quick questions (image gen, diagrams, deploy target, db, package manager, docs surface)
4. Writes `.ucw/knowledge/PREFERENCES.md`
5. Initialises `.ucw/memory.sqlite`
6. Enables stack-relevant plugins

Re-run via `/ucw prefs` to update any subset.

---

## Slash commands

Everything routes through the single `/ucw` root (see
`commands/ucw.md` for the authoritative dispatch table):

| Command | What it does |
|---|---|
| `/ucw status` | Token budget, memory stats, gate pass rate, open work item |
| `/ucw init` | Onboarding wizard |
| `/ucw prefs [categories]` | Re-run onboarding for specific preference categories |
| `/ucw plan <goal>` | Scope + plan, presents for approval (`[kimi]` task tags offload bounded tasks to Kimi K3 when `kimi.offload` is on) |
| `/ucw ship` | Verify + review in parallel → land (commit, push, optional PR) |
| `/ucw review` | Cross-audit review pipeline: parallel reviewer lanes → dedup → disprove → gate (`--with-kimi` adds the Kimi second-opinion lane; `--disprover-model kimi` routes the disprove step through Kimi) |
| `/ucw audit` | Security scan of UCW config itself |
| `/ucw ask <question>` | Repo-oracle Q&A grounded in knowledge docs + code |
| `/ucw opinion <q \| --diff>` | Ad-hoc Kimi second opinion (advisory, read-only) |
| `/ucw recall <q>` | Hybrid memory search |
| `/ucw pin <fact>` | Force-include in future sessions |
| `/ucw scribe` | Refresh `.ucw/knowledge/*` from the latest diff |
| `/ucw distill` | Promote instincts to skills |
| `/ucw dashboard` | Open observability UI |
| `/ucw watch <PR>` / `/ucw unwatch <PR>` | Subscribe/unsubscribe PR activity for autopilot fixes |
| `/ucw worktree <subcmd>` | Multi-agent worktree fan-out (create / list / cleanup / remove) |
| `/ucw phase <subcmd>` | Workflow phase get / set / clear |
| `/ucw auto <on [level] \| off \| status>` | Autonomous run mode (levels 1–4; `auto.default_level` sets the bare-`on` default) |
| `/ucw settings <verb>` | Per-project behavior toggles (list / get / set / unset) |
| `/ucw resume` | Re-orientation block after `/clear` or `/compact` |

---

## Build plan

See [`docs/architecture.md`](docs/architecture.md) for the full design.

| Milestone | Status |
|---|---|
| M1 — Skeleton, plugin manifest, installer, CI | ✅ shipped |
| M2 — Knowledge + Onboarding (templates, detect-stack, render-knowledge, scribe/onboarder agents) | ✅ shipped |
| M3 — Memory subsystem (SQLite + FTS5, MCP server, distillation, Claude + Voyage reranking, privacy) | ✅ shipped |
| M4 — Workflow + gates (phase tracking, inner-loop gates, streak breaker, phase-aware Stop, PR-watch via /ucw watch) | ✅ shipped |
| M5 — Orchestration + learning (instinct promotion → Skill drafts, worktree fan-out helper) | ✅ shipped |
| M6 — Observability + audit (color CLI dashboard, secret scanner, scribe diff helper, stale-doc checker, statusLine) | ✅ shipped |
| M7 — Marketplace publish | ⏳ |

**Test surface:** 700+ unit tests + end-to-end smoke + CI gates (ruff + shellcheck + JSON-validate + audit + pytest + smoke).
Run `make validate` for the full local sweep. Run `make smoke` to see it lay down a fake repo, render knowledge, distill, recall, and audit — all in ~3s.

---

## Credit

This synthesizes ideas from:
- [Superpowers](https://github.com/obra/superpowers) — phase enforcement
- [Everything Claude Code](https://github.com/affaan-m/everything-claude-code) — instincts, AgentShield
- [wshobson/agents](https://github.com/wshobson/agents) — plugin sharding, model tiering
- [claude_memory](https://github.com/codenamev/claude_memory) — trust panel, hybrid retrieval
- [Anthropic Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) — the embedding pipeline

---

## License

MIT.
