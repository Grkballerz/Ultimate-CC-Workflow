# Design

> Architecture overview + lightweight ADR log. The scribe appends entries on
> architectural shifts; structural rewrites pause for your OK.

## What this project does
UCW (Ultimate Claude Code Workflow) is a Claude Code plugin that wraps
feature work in a phased pipeline — Scope → Plan → Build → Verify → Land —
with a human-approval gate between Plan and Build. It fans review work out
across parallel subagent reviewer lanes, keeps a persistent memory MCP
server for facts and decisions, and maintains a set of knowledge docs
(this directory) that get kept in sync with the codebase by a scribe pass
after every Land.

## Key abstractions
- **Findings store** — review findings from every reviewer lane flow
  through one pipeline: disprove (an independent pass tries to invalidate
  each finding) → dedup → gate. A finding only blocks Land if it survives
  disprove and clears the gate; no single lane's output is load-bearing
  on its own (`bin/ucw-review.py`).
- **`kimi_invoke` bridge** (`bin/kimi_invoke.py`) — shared subprocess
  wrapper around `claude-kimi -p`, the headless call-out to Kimi K3. Never
  raises: timeout, nonzero exit, missing binary, and malformed output all
  come back as `{ok: False, error: ...}` instead of throwing. Supports a
  JSON-extraction mode (lenient, grabs the last balanced `{...}`/`[...]`
  block from stdout) and a raw mode for prose answers. Timeout and model
  resolve through the settings registry, env-var override first.
- **Settings registry** (`bin/ucw-settings.py`) — a typed, closed set of
  per-project behavior toggles persisted at `.ucw/state/settings.json`.
  Resolution order is `UCW_<KEY>` env var > project override > registry
  default. Deliberately holds no secrets — only feature flags/knobs
  (e.g. `kimi.offload`, `kimi.model`, `review.default`).

## Module / package layout
```
_run `tree -L 2 -I node_modules` and paste here_
```

## External dependencies & boundaries
- _to be filled in_

## Cross-cutting concerns (auth, logging, errors, config)
- _to be filled in_

---

## Decision Log (ADR-lite)

Format: `YYYY-MM-DD — Decision — Because …`

<!-- scribe-adr-start -->
- 2026-08-21 — Kimi K3 wired in as advisory-only second opinion (review lane, opinion cmd, disprover route, gated implementer offload) — Because cross-vendor challenge catches what same-vendor review misses; Kimi output is never load-bearing without passing existing gates, and all Kimi spend is opt-in via /ucw settings.
- 2026-08-23 — Gates fail loudly under strict conditions — Because a green verify that ran zero tools (missing pytest/ruff) under auto-commit was the audit's worst failure mode; strict = verify/land phase or auto-level >= 2.
- 2026-08-23 — Review lanes emit lane-done receipts; gate --expect-lanes fails on missing lanes — Because dead reviewers were indistinguishable from a clean review (empty store passed the gate).
- 2026-08-23 — Reviewer agents may hold Write only with audited write-scope frontmatter confined to .ucw/state/ — Because report files need writing but reviewers must not be able to tamper with reviewed sources; audit demotes to a kept-visible nit.
<!-- scribe-adr-end -->
