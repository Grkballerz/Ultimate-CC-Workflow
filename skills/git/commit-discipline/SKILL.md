---
name: commit-discipline
description: One logical change per commit, with a message that explains the why. Activate at the Land phase of every workflow run — the commit message is documentation that survives the PR.
when_to_use:
  - Always, at Land phase
  - Before pushing any change visible to others
when_not_to_use:
  - WIP commits on a personal feature branch you'll squash later (still
    follow the format, just relax the granularity)
---

# Commit Discipline

A commit is a unit of explanation. The diff says what; the message says why.

## Message format

```
<scope>: <short summary in imperative voice — 50 chars max>

<body, wrapping at 72 chars — explain WHY this change is needed,
not what changed. The diff already shows what.>

<optional footer: refs / breaking notes>
```

Examples:

```
memory: add Claude Haiku reranker fallback

When VOYAGE_API_KEY is absent, recall() falls back to FTS-only
ranking. That works but misses semantic matches users expect.
This wires Claude Haiku as a one-call judge over the top-30 FTS
hits, with the existing fuse(alpha=0.4) RRF logic.

The fallback only activates when ANTHROPIC_API_KEY is set;
otherwise we stay at fts-only.
```

```
audit: suppress audit-allow-tagged lines

Documentation files (like agents/security-reviewer.md) contain
illustrative examples of bad patterns. They legitimately match
the secret/injection regexes. Adding a comment-based opt-out
preserves the example without forcing a structural rewrite.
```

## Granularity

- **One commit = one logical change.** "Add feature X" is one commit even
  if it spans 5 files.
- **Refactor and feature are separate commits.** Move the function in
  commit A; change its behavior in commit B.
- **Test + code in the same commit.** They prove each other.
- **No "wip" / "fixes" / "address review" commits in the final history.**
  Squash before merge.

## Anti-patterns

- "Fix bug" — what bug? Why does this fix it?
- "Update README" — say what changed and why someone would want to know
- Commits that pass tests in isolation but break the build at HEAD — keep
  the history bisectable
- 1000-line commits — split

## After the commit

UCW runs the **scribe** subagent automatically (post-Land). Scribe diffs
your change against `.ucw/knowledge/*.md` and proposes updates:
- New dep → STACK.md
- New abstraction → DESIGN.md ADR entry
- New convention emerging in review → CONVENTIONS.md
- Roadmap item complete → ROADMAP.md tick

Let scribe do its work. If it proposes a structural rewrite of a doc, it
will pause for your approval per `PREFERENCES.scribe_mode`.
