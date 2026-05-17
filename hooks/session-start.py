#!/usr/bin/env python3
"""SessionStart hook — inject Knowledge INDEX and recently-pinned memory facts.

Reads stdin (JSON from Claude Code), writes JSON to stdout with
`hookSpecificOutput.additionalContext`. Non-blocking by design.

This is the M1 stub — wired into settings/standard.json and settings/full.json.
M3 will replace the placeholder memory section with a real recall() call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def find_project_root(cwd: Path) -> Path:
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists() or (parent / ".ucw").exists():
            return parent
    return cwd


def load_knowledge_index(project_root: Path) -> str | None:
    index = project_root / ".ucw" / "knowledge" / "INDEX.md"
    if not index.exists():
        return None
    try:
        return index.read_text(encoding="utf-8")
    except OSError:
        return None


def suggest_init() -> str:
    return (
        "**UCW:** no `.ucw/` directory detected in this project. "
        "Run `/ucw init` to bootstrap Knowledge + Memory for this repo."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}

    cwd = Path(payload.get("cwd", os.getcwd()))
    project_root = find_project_root(cwd)
    index = load_knowledge_index(project_root)

    if index is None:
        additional_context = suggest_init()
    else:
        # M3 will append memory.recall() results here.
        additional_context = f"## UCW Knowledge\n\n{index}"

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }
    json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
