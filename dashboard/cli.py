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

try:
    from ucw_memory.findings import ReviewStore  # type: ignore[import-not-found]
except ImportError:
    ReviewStore = None  # type: ignore[assignment,misc]


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


_VALID_AUTO_LEVELS = (1, 2, 3, 4)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _auto_mode(ucw: Path) -> dict:
    """Auto-mode snapshot from the same state bin/ucw-auto.py uses.

    Resolution mirrors ucw-auto: UCW_AUTO_MODE env → .ucw/state/auto-mode
    → off. Retry cap: UCW_AUTO_RETRY_CAP env → state `retry_cap` →
    `auto.retry_cap` setting → 3.
    """
    state = _read_json(ucw / "state" / "auto-mode")
    env = os.environ.get("UCW_AUTO_MODE", "").strip().lower()
    level, source = 0, "state"
    if env in {"0", "off", "no", "false"}:
        level, source = 0, "env"
    elif env.isdigit() and int(env) in _VALID_AUTO_LEVELS:
        level, source = int(env), "env"
    elif env == "on":
        level, source = 4, "env"
    else:
        stored = state.get("level")
        if isinstance(stored, int) and stored in _VALID_AUTO_LEVELS:
            level = stored
    if level == 0:
        return {"enabled": False, "level": 0}

    cap = 3
    settings_cap = _read_json(ucw / "state" / "settings.json").get("auto.retry_cap")
    if (isinstance(settings_cap, int) and not isinstance(settings_cap, bool)
            and 0 < settings_cap < 100):
        cap = settings_cap
    state_cap = state.get("retry_cap")
    if (isinstance(state_cap, int) and not isinstance(state_cap, bool)
            and 0 < state_cap < 100):
        cap = state_cap
    env_cap = os.environ.get("UCW_AUTO_RETRY_CAP", "").strip()
    if env_cap.isdigit() and 0 < int(env_cap) < 100:
        cap = int(env_cap)

    try:
        retries = int(_read_state_file(ucw, "auto-retries") or "0")
    except ValueError:
        retries = 0
    return {
        "enabled": True,
        "level": level,
        "source": source,
        "since": state.get("since"),
        "retry_cap": cap,
        "retries_used": retries,
    }


def _head_sha(repo_root: Path) -> str | None:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def _review_gate(repo_root: Path) -> dict | None:
    """Open findings + effective-critical count for HEAD from .ucw/reviews/.

    None ⇒ no review recorded for the current HEAD (renders as "none").
    Severity math stays in ucw_memory.findings — never recomputed here.
    """
    reviews_dir = repo_root / ".ucw" / "reviews"
    if ReviewStore is None or not reviews_dir.is_dir():
        return None
    sha = _head_sha(repo_root)
    if sha is None or not (reviews_dir / sha).is_dir():
        return None
    try:
        summary = ReviewStore(repo_root, sha).gate_summary()
    except Exception:
        return None
    return {
        "sha": sha,
        "open_findings": summary.get("total", 0),
        "effective_critical": summary.get("by_severity", {}).get("critical", 0),
        "unack_critical": summary.get("unack_critical", 0),
    }


def _distill_health(ucw: Path) -> dict:
    """Detect a silently broken memory-distill pipeline from .ucw/sessions.log.

    A batch is a session-log line with distill_jobs >= 1 (a session that
    had nothing queued proves nothing). When the trailing 3+ batches all
    wrote zero facts, surface a warning.
    """
    batches: list[int] = []
    log_path = ucw / "sessions.log"
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            fields: dict[str, str] = {}
            for part in line.strip().split("\t"):
                if "=" in part:
                    key, _, value = part.partition("=")
                    fields[key] = value
            try:
                jobs = int(fields.get("distill_jobs", "0"))
                facts = int(fields.get("facts_written", "0"))
            except ValueError:
                continue
            if jobs >= 1:
                batches.append(facts)
    zero_streak = 0
    for facts in reversed(batches):
        if facts != 0:
            break
        zero_streak += 1
    warning = None
    if zero_streak >= 3:
        warning = (f"memory distill has extracted 0 facts in {zero_streak} "
                   f"batches — pipeline may be broken")
    return {"batches": len(batches), "zero_streak": zero_streak,
            "warning": warning}


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
    auto_mode = _auto_mode(ucw)
    review_gate = _review_gate(repo_root)
    distill_health = _distill_health(ucw)

    payload = {
        "ucw":            str(ucw),
        "phase":          phase if phase != "(none)" else None,
        "edit_streak":    streak,
        "auto_mode":      auto_mode,
        "review_gate":    review_gate,
        "distill_health": distill_health,
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

    if auto_mode["enabled"]:
        auto_txt = f"level {auto_mode['level']}"
        if auto_mode.get("since"):
            auto_txt += f" since {auto_mode['since']}"
        auto_txt += f" — retries {auto_mode['retries_used']}/{auto_mode['retry_cap']}"
        print(f"  auto mode         {paint(auto_txt, Color.MAGENTA)}")
    else:
        print(f"  auto mode         {paint('off', Color.DIM)}")

    if review_gate is None:
        print(f"  review gate       {paint('none', Color.DIM)}")
    else:
        gate_txt = (f"{review_gate['open_findings']} open finding(s), "
                    f"{review_gate['effective_critical']} effective-critical "
                    f"(sha {review_gate['sha'][:8]})")
        gate_color = Color.RED if review_gate["effective_critical"] else Color.GREEN
        print(f"  review gate       {paint(gate_txt, gate_color)}")
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
        print(paint(f"  ⚠ stale docs      {len(stale)} need refresh — run /ucw scribe", Color.YELLOW))
        for s in stale:
            print(f"    {s['doc']}: {len(s.get('reasons', []))} trigger(s)")
    else:
        print(f"  stale docs        {paint('fresh ✓', Color.GREEN)}")

    if distill_health["warning"]:
        print(paint(f"  ⚠ distill health  {distill_health['warning']}", Color.YELLOW))
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
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
