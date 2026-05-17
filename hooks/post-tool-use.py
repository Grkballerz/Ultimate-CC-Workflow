#!/usr/bin/env python3
"""PostToolUse hook for Edit | Write.

Runs lightweight gates on the file that was just edited. The goal is the
**inner loop**: surface obvious problems in the very next turn, not wait for
the full Verify phase.

M1 baseline:
- Track edit-streak counter so post-tool-batch can break it.
- If the file is Python: `python -m py_compile` for a syntax check.
- If the file is JSON: `json.tool` parse check.
- If the file is YAML and PyYAML is available: parse check.
- For other languages: skip silently (the project's PostToolUse hook can hook in).

Future (M4+): full lint/typecheck dispatch per file extension, using the
project's configured tools from `.ucw/knowledge/PREFERENCES.md`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, project_root, read_payload, state_file, write_output


def _increment_edit_streak(payload: dict) -> int:
    sf = state_file(payload, "edit-streak")
    sf.parent.mkdir(parents=True, exist_ok=True)
    current = 0
    if sf.exists():
        try:
            current = int(sf.read_text().strip() or "0")
        except (OSError, ValueError):
            current = 0
    current += 1
    sf.write_text(str(current))
    return current


def _check_python(file_path: Path) -> str | None:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(file_path)],
        capture_output=True, text=True, timeout=8,
    )
    if result.returncode != 0:
        return result.stderr.strip()
    return None


def _check_json(file_path: Path) -> str | None:
    try:
        json.loads(file_path.read_text(encoding="utf-8"))
        return None
    except (OSError, json.JSONDecodeError) as exc:
        return str(exc)


def _check_yaml(file_path: Path) -> str | None:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        yaml.safe_load(file_path.read_text(encoding="utf-8"))
        return None
    except Exception as exc:  # broad — yaml.YAMLError + IO
        return str(exc)


CHECKERS = {
    ".py":   _check_python,
    ".json": _check_json,
    ".yaml": _check_yaml,
    ".yml":  _check_yaml,
}


def main() -> int:
    payload = read_payload()
    tool_input = payload.get("tool_input", {}) or {}
    file_arg = tool_input.get("file_path") or tool_input.get("path")
    if not file_arg:
        return 0

    file_path = Path(file_arg)
    if not file_path.is_absolute():
        file_path = project_root(payload) / file_path
    if not file_path.exists():
        return 0

    streak = _increment_edit_streak(payload)
    log(payload, f"edit #{streak} on {file_path.name}")

    checker = CHECKERS.get(file_path.suffix.lower())
    if checker is None:
        return 0

    try:
        error = checker(file_path)
    except subprocess.TimeoutExpired:
        return 0

    if error:
        write_output({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"⚠️ UCW inner-loop check failed for `{file_path.name}`:\n"
                    f"```\n{error[:1000]}\n```\n"
                    f"Fix this before moving to the next task."
                ),
            }
        })
    return 0


if __name__ == "__main__":
    sys.exit(main())
