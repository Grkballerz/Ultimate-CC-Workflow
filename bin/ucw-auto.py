#!/usr/bin/env python3
"""Read or write UCW's auto-mode level.

Auto-mode lets the agent run unattended through phase boundaries that
would normally pause for user approval. There are four cumulative levels:

    1 = planner auto-accepts its own spec + plan and starts Build
    2 = + retry loop on verify failure (cap: UCW_AUTO_RETRY_CAP env >
        `on --retry-cap` state > auto.retry_cap setting > 3)
    3 = + auto-commit + auto-push when verify passes
    4 = + auto-open draft PR + subscribe to PR activity for CI autofix

State lives at `.ucw/state/auto-mode` as JSON:
    {"level": <int>, "since": "<ISO 8601>", "retry_cap": <int|null>}

Hooks and commands MUST read via `current_level(project_root)` rather than
parsing the file directly — that keeps the schema migratable.

Usage:
    ucw-auto.py on [level]      # default: auto.default_level setting, else 4
    ucw-auto.py off             # clear state, disable
    ucw-auto.py status          # print current state (JSON)
    ucw-auto.py level           # print only the integer level (0 if off)

Bare `on` (no level argument) resolves its level as
UCW_AUTO_DEFAULT_LEVEL env > `auto.default_level` in the project's
`.ucw/state/settings.json` > 4. An explicit level argument always wins.
The settings file is read inline (same convention as kimi_invoke) —
ucw-settings.py owns the registry.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

VALID_LEVELS = (1, 2, 3, 4)
DEFAULT_LEVEL_ON_BARE_ON = 4  # `/ucw auto on` with no number = full auto
STATE_FILENAME = "auto-mode"
SETTINGS_FILENAME = "settings.json"
DEFAULT_LEVEL_ENV = "UCW_AUTO_DEFAULT_LEVEL"


def _project_root(start: Path | None = None) -> Path:
    cwd = start or Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir() or (parent / ".git").exists():
            return parent
    return cwd


def _state_path(project_root: Path | None = None) -> Path:
    root = project_root or _project_root()
    return root / ".ucw" / "state" / STATE_FILENAME


def _read_settings(project_root: Path | None = None) -> dict:
    """Project settings.json as a dict ({} on any failure).

    Read inline rather than via ucw-settings.py — that file's hyphenated
    name defeats plain import (same convention as kimi_invoke).
    """
    root = project_root or _project_root()
    sp = root / ".ucw" / "state" / SETTINGS_FILENAME
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def default_on_level(project_root: Path | None = None) -> int:
    """Level bare `on` uses when no argument is given.

    Resolution: UCW_AUTO_DEFAULT_LEVEL env > `auto.default_level` setting
    > DEFAULT_LEVEL_ON_BARE_ON. Out-of-range values fall through.
    """
    env = os.environ.get(DEFAULT_LEVEL_ENV, "").strip()
    if env.isdigit() and int(env) in VALID_LEVELS:
        return int(env)
    stored = _read_settings(project_root).get("auto.default_level")
    if (isinstance(stored, int) and not isinstance(stored, bool)
            and stored in VALID_LEVELS):
        return stored
    return DEFAULT_LEVEL_ON_BARE_ON


def current_level(project_root: Path | None = None) -> int:
    """Return the active auto-mode level (0 if disabled). Hooks call this.

    Resolution order (first hit wins):
      1. `UCW_AUTO_MODE` env var (escape hatch — wins so a panicking user
         can disable in one shell without editing files)
      2. `.ucw/state/auto-mode` JSON
      3. 0 (off)

    UCW_AUTO_MODE=0 / off / no / false forces OFF regardless of state file.
    """
    env = os.environ.get("UCW_AUTO_MODE", "").strip().lower()
    if env in {"0", "off", "no", "false", ""}:
        if env in {"0", "off", "no", "false"}:
            return 0
        # Empty env → fall through to state file
    elif env.isdigit() and int(env) in VALID_LEVELS:
        return int(env)
    elif env == "on":
        return DEFAULT_LEVEL_ON_BARE_ON

    sp = _state_path(project_root)
    if not sp.exists():
        return 0
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    level = data.get("level")
    if isinstance(level, int) and level in VALID_LEVELS:
        return level
    return 0


def current_retry_cap(project_root: Path | None = None) -> int:
    """Retry cap for the level-2+ verify loop (default 3).

    Resolution: `UCW_AUTO_RETRY_CAP` env > `retry_cap` in the auto-mode
    state file (`on --retry-cap`) > `auto.retry_cap` setting > 3.
    Must stay in agreement with hooks/_hook_common.auto_retry_cap().
    """
    env = os.environ.get("UCW_AUTO_RETRY_CAP", "").strip()
    if env.isdigit():
        cap = int(env)
        if 0 < cap < 100:
            return cap
    sp = _state_path(project_root)
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            cap = data.get("retry_cap")
            if isinstance(cap, int) and 0 < cap < 100:
                return cap
        except (OSError, json.JSONDecodeError):
            pass
    stored = _read_settings(project_root).get("auto.retry_cap")
    if (isinstance(stored, int) and not isinstance(stored, bool)
            and 0 < stored < 100):
        return stored
    return 3


def cmd_on(args: argparse.Namespace) -> int:
    level = args.level if args.level is not None else default_on_level()
    if level not in VALID_LEVELS:
        print(json.dumps({"error": f"invalid level: {level}",
                          "valid": list(VALID_LEVELS)}), file=sys.stderr)
        return 2
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "level": level,
        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if args.retry_cap is not None:
        payload["retry_cap"] = args.retry_cap
    sp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"auto_mode": "on", **payload, "path": str(sp)}))
    return 0


def cmd_off(_: argparse.Namespace) -> int:
    sp = _state_path()
    existed = sp.exists()
    if existed:
        sp.unlink()
    print(json.dumps({"auto_mode": "off", "cleared": existed}))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    sp = _state_path()
    level = current_level()
    if level == 0:
        print(json.dumps({"auto_mode": "off", "level": 0}))
        return 0
    out = {"auto_mode": "on", "level": level, "retry_cap": current_retry_cap()}
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            if "since" in data:
                out["since"] = data["since"]
        except (OSError, json.JSONDecodeError):
            pass
    if os.environ.get("UCW_AUTO_MODE"):
        out["source"] = "env"
    else:
        out["source"] = "state"
    print(json.dumps(out))
    return 0


def cmd_level(_: argparse.Namespace) -> int:
    print(current_level())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-auto")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_on = sub.add_parser("on")
    p_on.add_argument("level", nargs="?", type=int, default=None,
                      help=f"auto-mode level {list(VALID_LEVELS)} "
                           f"(default: auto.default_level setting, "
                           f"else {DEFAULT_LEVEL_ON_BARE_ON})")
    p_on.add_argument("--retry-cap", type=int, default=None,
                      help="override retry cap for level 2+ "
                           "(default: auto.retry_cap setting, else 3)")
    p_on.set_defaults(func=cmd_on)

    p_off = sub.add_parser("off")
    p_off.set_defaults(func=cmd_off)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    p_level = sub.add_parser("level")
    p_level.set_defaults(func=cmd_level)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
