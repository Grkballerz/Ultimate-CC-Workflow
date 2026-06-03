"""Shared utilities for UCW hook scripts.

All hooks share: stdin JSON parsing, project-root resolution, structured stdout
output, and best-effort logging to `.ucw/hooks.log` for debugging.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def read_payload() -> dict[str, Any]:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def write_output(output: dict[str, Any]) -> None:
    json.dump(output, sys.stdout)


def project_root(payload: dict[str, Any]) -> Path:
    cwd = Path(payload.get("cwd", os.getcwd()))
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists() or (parent / ".ucw").is_dir():
            return parent
    return cwd


def ucw_dir(payload: dict[str, Any]) -> Path:
    return project_root(payload) / ".ucw"


def log(payload: dict[str, Any], message: str) -> None:
    """Best-effort log line to .ucw/hooks.log. Never throws."""
    try:
        d = ucw_dir(payload)
        d.mkdir(parents=True, exist_ok=True)
        log_file = d / "hooks.log"
        with log_file.open("a", encoding="utf-8") as fh:
            event = payload.get("hook_event_name", "?")
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event}: {message}\n")
    except OSError:
        pass


def state_file(payload: dict[str, Any], name: str) -> Path:
    """Path under .ucw/state/. Caller is responsible for mkdir if writing."""
    return ucw_dir(payload) / "state" / name


# ---- auto-mode helpers ----------------------------------------------------
# Inlined rather than imported from bin/ucw-auto.py because (a) bin/ is a
# CLI shipping with a hyphen in the name (not importable) and (b) hooks
# should stay self-contained for testability. Schema must match the file
# bin/ucw-auto.py writes; tests in test_auto_mode_integration.py guard
# against drift.

_VALID_AUTO_LEVELS = (1, 2, 3, 4)


def auto_mode_level(payload: dict[str, Any]) -> int:
    """Return the current auto-mode level (0 = off).

    Resolution order: UCW_AUTO_MODE env → .ucw/state/auto-mode → 0.
    """
    env = os.environ.get("UCW_AUTO_MODE", "").strip().lower()
    if env in {"0", "off", "no", "false"}:
        return 0
    if env.isdigit() and int(env) in _VALID_AUTO_LEVELS:
        return int(env)
    if env == "on":
        return 4
    sf = state_file(payload, "auto-mode")
    if not sf.exists():
        return 0
    try:
        import json
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    level = data.get("level")
    if isinstance(level, int) and level in _VALID_AUTO_LEVELS:
        return level
    return 0


def auto_retry_cap(payload: dict[str, Any]) -> int:
    """Return the retry cap (default 3). Used by Stop hook at level >= 2."""
    env = os.environ.get("UCW_AUTO_RETRY_CAP", "").strip()
    if env.isdigit():
        cap = int(env)
        if 0 < cap < 100:
            return cap
    sf = state_file(payload, "auto-mode")
    if sf.exists():
        try:
            import json
            data = json.loads(sf.read_text(encoding="utf-8"))
            cap = data.get("retry_cap")
            if isinstance(cap, int) and 0 < cap < 100:
                return cap
        except (OSError, ValueError):
            pass
    return 3
