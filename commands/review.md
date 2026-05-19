---
description: Cross-audit code review of the current diff. Narrow-scope reviewers in parallel, then a disprover challenges every finding, then reachability is determined separately for security findings. Effective severity comes from the deterministic gate, not from any single agent.
argument-hint: "[--full | --quick | --narrow <area> | --since <ref> | approve <id> | status]"
---

Arguments: $ARGUMENTS

# Dispatch

Inspect $ARGUMENTS:

- **empty OR contains `--full`** → run **Full review** (the default pipeline)
- `--quick` → run **Quick review** (single pass, deterministic audit only)
- `--narrow <area>` → run Full review but only for the named concern
- `--since <ref>` → use that ref as the diff base (default: `main`)
- `approve <id> [--reason ...]` → run **Approve** path
- `status` → run **Status** path (no agents, just report)
- `list` → call `ucw-review.py list`

---

# Full review (default)

The goal is to produce a high-signal review by running **many narrow agents
in parallel**, then having a **different model disprove each finding**,
and separately deciding **reachability** for security findings. Effective
severity is computed by deterministic code, not by any single agent.

## Step 1 — compute scopes

```
$HOME/.claude/ucw/bin/ucw-review.py scope --persist
  ${SINCE:+--since "$SINCE"}
```

This emits a JSON list of `{file, concern}` pairs. Read it. **Don't fan out
to all of them blindly** — group by concern and dispatch one Task per
concern, passing the file list to the narrow reviewer.

## Step 2 — narrow-scope fan-out (parallel)

For each concern with at least one file, invoke its reviewer subagent in
parallel via the **Task** tool (one Task call per concern, batched in a
single message so they run concurrently):

| concern | subagent_type | model |
|---|---|---|
| correctness | reviewer-correctness | sonnet |
| injection | reviewer-injection | sonnet |
| deserialization | reviewer-deserialization | sonnet |
| auth | reviewer-auth | sonnet |
| performance | reviewer-performance | sonnet |
| data-loss | reviewer-data-loss | sonnet |
| api-compat | reviewer-api-compat | sonnet |
| tests | reviewer-tests | sonnet |
| docs | reviewer-docs | sonnet |

Pass each reviewer:
- The list of files in scope for its concern
- The diff: `git diff $BASE..HEAD -- <files>`
- The plan (if any): contents of `.ucw/state/plan.md`

Each reviewer will call `ucw-review.py add-finding ...` for any defect it
finds. The findings persist to `.ucw/reviews/<sha>/findings.jsonl`.

## Step 3 — disprove every finding

Once narrow reviewers are done, query the finding store:

```
$HOME/.claude/ucw/bin/ucw-review.py summary --format json
```

For **each finding**, invoke the **disprover** subagent (Task tool) in
parallel batches. Pass the finding's id, file, line, and detail. The
disprover is `model: haiku` so it's a different model from the sonnet
finders — that's the cross-audit. The disprover calls
`ucw-review.py disprove <id> <verdict> ...` to record its verdict.

## Step 4 — reachability for security findings

For every finding where `category in {injection, deserialization, auth}`,
invoke the **reachability** subagent (Task tool). It traces from external
entry points to the bug site and records the verdict via
`ucw-review.py reachability <id> <verdict> ...`.

## Step 5 — dedup

```
$HOME/.claude/ucw/bin/ucw-review.py dedup
```

Merges near-duplicate findings (same file+line+category, similar title).

## Step 6 — gate

```
$HOME/.claude/ucw/bin/ucw-review.py gate
```

Exit 2 = unack'd critical(s) — `/ship` will be blocked. Exit 0 = clean.

## Step 7 — report

```
$HOME/.claude/ucw/bin/ucw-review.py summary --format markdown
```

Show the user the rendered table grouped by severity. For any
unacknowledged critical, surface the suggested approval command:
`/review approve <id> --reason "..."`.

---

# Quick review

Single-pass single-agent + deterministic audit only — for trivial diffs:

1. `ucw-review.py scope --persist`
2. Invoke the **reviewer** subagent (the original general-purpose one) on
   the whole diff
3. `ucw-audit.py --repo .` for the deterministic security scan
4. `ucw-review.py gate`

---

# Approve

```
$HOME/.claude/ucw/bin/ucw-review.py approve <id> --reason "$REASON" --actor "$USER"
```

Use AskUserQuestion if `<reason>` was not provided.

---

# Status

```
$HOME/.claude/ucw/bin/ucw-review.py status
```

No agents are invoked. This is the at-a-glance "what does the gate think
right now" snapshot.

---

# What this command is designed against

Single-agent reviews wander aimlessly and bury 90% of useful findings in
context-window noise. Multi-agent without cross-audit produces high
false-positive rates that train you to ignore the system. We use:

- **Narrow scope per agent** so each one goes deep on one concern
- **Different model for the disprover** so it doesn't share the original
  agent's framing bias
- **Chain split** so "is it broken?" and "is it reachable?" are answered
  separately and never confused
- **Deterministic governance** — the gate's verdict comes from immutable
  persisted records and an explicit severity-from-verdicts function in
  `memory/ucw_memory/findings.py`, not from any model's free-text claim

This is the Cloudflare pattern. Read `memory/ucw_memory/findings.py` if
you want to know exactly how effective severity is computed.
