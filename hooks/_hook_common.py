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
