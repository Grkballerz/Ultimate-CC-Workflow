#!/usr/bin/env python3
"""UCW text dashboard.

    python -m dashboard.cli status        # snapshot
    python -m dashboard.cli status --json # machine-readable

Shows phase, edit streak, memory counts, knowledge freshness, stale-doc
warnings, and active embedding mode. Color is enabled automatically when
stdout is a TTY; pass --no-color to force plain output.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "memory"))

try:
    from ucw_memory.db import MemoryDB  # type: ignore[import-not-found]
except ImportError:
    MemoryDB = None  # type: ignore[assignment,misc]


# ---- ANSI -------------------------------------------------------------------

class Color:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"


def _stripped(_text: str) -> str:
    return _text


def _make_paint(enabled: bool):
    if enabled:
        def paint(text: str, color: str) -> str:
            return f"{color}{text}{Color.RESET}"
        return paint
    return lambda text, _: text


# ---- helpers ----------------------------------------------------------------

def _find_ucw(start: Path | None = None) -> Path | None:
    cwd = start or Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir():
            return parent / ".ucw"
    return None


def _global_ucw() -> Path:
    return Path(os.environ.get("UCW_MEMORY_HOME", Path.home() / ".claude" / "ucw"))


def _stats_for(db_path: Path) -> dict:
    if MemoryDB is None or not db_path.exists():
        return {"db_path": str(db_path), "exists": False, "total": 0, "pinned": 0}
    try:
        with MemoryDB(db_path) as db:
            s = db.stats()
            s["exists"] = True
            return s
    except Exception as exc:
        return {"db_path": str(db_path), "exists": False, "error": str(exc), "total": 0, "pinned": 0}


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
        return {"exists": False, "count": 0, "files": []}
    files = sorted(p.name for p in knowledge.glob("*.md"))
    return {"exists": True, "count": len(files), "files": files}


def _stale_docs(repo_root: Path) -> list[dict]:
    """Invoke ucw-knowledge-check.py if available; return [] otherwise."""
    helper = REPO_ROOT / "bin" / "ucw-knowledge-check.py"
    if not helper.exists():
        # try the installed location too
        helper = Path.home() / ".claude" / "ucw" / "bin" / "ucw-knowledge-check.py"
    if not helper.exists():
        return []
    try:
        cp = subprocess.run(
            [sys.executable, str(helper), "--repo", str(repo_root), "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if cp.returncode in (0, 1):
            return json.loads(cp.stdout or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return []


def _embedding_mode() -> str:
    """Best-guess mode reflecting what recall() would do *right now*."""
    if os.environ.get("VOYAGE_API_KEY"):
        return "fts+rerank-voyage"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "fts+rerank-claude"
    return "fts-only"


# ---- commands ----------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    ucw = _find_ucw()
    if ucw is None:
        if args.json:
            print(json.dumps({"error": "no .ucw/ in this project"}))
        else:
            print("no .ucw/ directory in this project — run /ucw init")
        return 1

    repo_root = ucw.parent
    proj_stats = _stats_for(ucw / "memory.sqlite")
    global_stats = _stats_for(_global_ucw() / "memory.sqlite")
    phase = _read_state_file(ucw, "phase") or "(none)"
    streak_raw = _read_state_file(ucw, "edit-streak") or "0"
    try:
        streak = int(streak_raw)
    except ValueError:
        streak = 0
    knowledge = _knowledge_freshness(ucw)
    stale = _stale_docs(repo_root)
    embedding_mode = _embedding_mode()

    payload = {
        "ucw":            str(ucw),
        "phase":          phase if phase != "(none)" else None,
        "edit_streak":    streak,
        "memory":         {"project": proj_stats, "global": global_stats},
        "knowledge":      knowledge,
        "stale":          stale,
        "embedding_mode": embedding_mode,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    color_on = sys.stdout.isatty() and not args.no_color
    paint = _make_paint(color_on)

    def section(title: str) -> None:
        print(paint(title, Color.BOLD + Color.CYAN))

    section(f"UCW status — {ucw}")
    print(paint("─" * 60, Color.DIM))
    print(f"  phase             {paint(phase, Color.BLUE if phase != '(none)' else Color.DIM)}")

    streak_color = Color.RED if streak >= 5 else (Color.YELLOW if streak >= 3 else Color.GREEN)
    print(f"  edit streak       {paint(str(streak), streak_color)}{paint(' (breaker fires at 5)', Color.DIM) if streak >= 3 else ''}")
    print()

    print(f"  memory (project)  {proj_stats.get('total', 0)} facts, "
          f"{paint(str(proj_stats.get('pinned', 0)), Color.MAGENTA)} pinned")
    print(f"  memory (global)   {global_stats.get('total', 0)} facts, "
          f"{paint(str(global_stats.get('pinned', 0)), Color.MAGENTA)} pinned")
    print(f"  embedding mode    {paint(embedding_mode, Color.GREEN if 'rerank' in embedding_mode else Color.DIM)}")
    print()

    if knowledge["exists"]:
        print(f"  knowledge docs    {knowledge['count']} files")
        for f in knowledge["files"]:
            print(f"                      - {f}")
    else:
        print(f"  knowledge docs    {paint('(none — run /ucw init)', Color.YELLOW)}")
    print()

    if stale:
        print(paint(f"  ⚠ stale docs      {len(stale)} need refresh — run /scribe", Color.YELLOW))
        for s in stale:
            print(f"    {s['doc']}: {len(s.get('reasons', []))} trigger(s)")
    else:
        print(f"  stale docs        {paint('fresh ✓', Color.GREEN)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="snapshot of phase, streak, memory, knowledge")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--no-color", action="store_true")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
