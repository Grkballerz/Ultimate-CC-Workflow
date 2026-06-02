#!/usr/bin/env python3
"""SessionStart hook — side-effect only (logging, state-touch).

Claude Code's strict hook output schema only accepts `hookSpecificOutput`
on PreToolUse / UserPromptSubmit / PostToolUse / PostToolBatch. SessionStart
isn't on that list — any `hookSpecificOutput.additionalContext` we tried
to emit was being rejected with `Invalid input` by the harness.

The previous design injected the contents of `.ucw/knowledge/INDEX.md` and
a "run /ucw init" hint into Claude's context at startup. Since that
channel doesn't exist in the strict schema, this hook is now side-effect-
only: it logs the session start to `.ucw/state/last-session-start` and
exits with no JSON output.

Knowledge / memory still get surfaced to Claude — just by different
mechanisms that ARE in-schema:

  - hooks/user-prompt-submit.py keyword-matches the prompt and injects
    relevant `.ucw/knowledge/*.md` via the UserPromptSubmit
    `additionalContext` field (this is allowed)
  - the `/ucw status` command renders the inventory on demand
  - `mcp__ucw-memory__memory.recall` is callable any time
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def find_project_root(cwd: Path) -> Path:
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists() or (parent / ".ucw").exists():
            return parent
    return cwd


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    cwd = Path(payload.get("cwd", os.getcwd()))
    project_root = find_project_root(cwd)
    state_dir = project_root / ".ucw" / "state"

    # Best-effort log — never crash this hook.
    try:
        if state_dir.exists() or (project_root / ".ucw").exists():
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "last-session-start").write_text(
                f"{int(time.time())}\t{payload.get('session_id', '?')}\t"
                f"{payload.get('source', payload.get('matcher', '?'))}\n",
                encoding="utf-8",
            )
    except OSError:
        pass

    # No JSON output — strict schema rejects hookSpecificOutput on SessionStart,
    # and there's no top-level field that injects context for this event.
    return 0


if __name__ == "__main__":
    sys.exit(main())
