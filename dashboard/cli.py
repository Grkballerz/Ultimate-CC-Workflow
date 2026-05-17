#!/usr/bin/env python3
"""UCW text dashboard — `python -m dashboard.cli status`.

M1 baseline: counts pinned facts, total facts, last session, edit streak,
phase, and stale-knowledge warning. The Flask web UI in M6+ uses the same
underlying queries.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "memory"))

try:
    from ucw_memory.db import MemoryDB  # type: ignore[import-not-found]
except ImportError:
    MemoryDB = None  # type: ignore[assignment,misc]


def _find_ucw() -> Path | None:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir():
            return parent / ".ucw"
    return None


def _global_ucw() -> Path:
    return Path(os.environ.get("UCW_MEMORY_HOME", Path.home() / ".claude" / "ucw"))


def _stats_for(db_path: Path) -> dict:
    if MemoryDB is None or not db_path.exists():
        return {"db_path": str(db_path), "exists": False}
    with MemoryDB(db_path) as db:
        s = db.stats()
        s["exists"] = True
        return s


def _read_state_file(ucw: Path, name: str) -> str | None:
    sf = ucw / "state" / name
    if not sf.exists():
        return None
    try:
        return sf.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _knowledge_freshness(ucw: Path) -> dict:
    knowledge = ucw / "knowledge"
    if not knowledge.is_dir():
        return {"exists": False}
    files = sorted(knowledge.glob("*.md"))
    return {
        "exists": True,
        "count": len(files),
        "files": [f.name for f in files],
    }


def cmd_status(args: argparse.Namespace) -> int:
    ucw = _find_ucw()
    if ucw is None:
        print("no .ucw/ directory in this project — run /ucw init")
        return 1

    proj_stats = _stats_for(ucw / "memory.sqlite")
    global_stats = _stats_for(_global_ucw() / "memory.sqlite")
    phase = _read_state_file(ucw, "phase") or "(none)"
    streak = _read_state_file(ucw, "edit-streak") or "0"
    knowledge = _knowledge_freshness(ucw)

    if args.json:
        print(json.dumps({
            "ucw": str(ucw),
            "phase": phase,
            "edit_streak": int(streak),
            "memory": {"project": proj_stats, "global": global_stats},
            "knowledge": knowledge,
        }, indent=2))
        return 0

    print(f"UCW status — {ucw}")
    print("─" * 60)
    print(f"  phase:            {phase}")
    print(f"  edit streak:      {streak}")
    print()
    print(f"  memory (project): {proj_stats.get('total', 0)} facts, {proj_stats.get('pinned', 0)} pinned")
    print(f"  memory (global):  {global_stats.get('total', 0)} facts, {global_stats.get('pinned', 0)} pinned")
    print(f"  embedding mode:   fts-only (M3 vec/voyage not yet wired)")
    print()
    if knowledge["exists"]:
        print(f"  knowledge docs:   {knowledge['count']} files")
        for f in knowledge["files"]:
            print(f"                      - {f}")
    else:
        print("  knowledge docs:   (none — run /ucw init)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="snapshot of phase, streak, memory, knowledge")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
