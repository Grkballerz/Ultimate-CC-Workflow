#!/usr/bin/env python3
"""Read or write the current UCW workflow phase.

Phases: scope | plan | build | verify | land | (cleared)

The phase lives in `.ucw/state/phase`. Commands (planner, implementer, ship)
call this helper to advance the state machine, and hooks (stop, post-tool-batch)
read it to decide whether to block.

Usage:
    ucw-phase.py get
    ucw-phase.py set <phase>
    ucw-phase.py clear
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VALID_PHASES = {"scope", "plan", "build", "verify", "land"}


def _project_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir() or (parent / ".git").exists():
            return parent
    return cwd


def _phase_file() -> Path:
    return _project_root() / ".ucw" / "state" / "phase"


def cmd_get(_: argparse.Namespace) -> int:
    pf = _phase_file()
    phase = pf.read_text(encoding="utf-8").strip() if pf.exists() else ""
    print(json.dumps({"phase": phase or None}))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    phase = args.phase.lower()
    if phase not in VALID_PHASES:
        print(json.dumps({"error": f"invalid phase: {phase}", "valid": sorted(VALID_PHASES)}), file=sys.stderr)
        return 2
    pf = _phase_file()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(phase + "\n", encoding="utf-8")
    print(json.dumps({"phase": phase, "path": str(pf)}))
    return 0


def cmd_clear(_: argparse.Namespace) -> int:
    pf = _phase_file()
    if pf.exists():
        pf.unlink()
    print(json.dumps({"phase": None, "cleared": True}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-phase")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_get = sub.add_parser("get")
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set")
    p_set.add_argument("phase", choices=sorted(VALID_PHASES))
    p_set.set_defaults(func=cmd_set)

    p_clear = sub.add_parser("clear")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
