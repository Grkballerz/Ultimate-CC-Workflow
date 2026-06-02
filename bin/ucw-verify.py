#!/usr/bin/env python3
"""Project-aware verification runner — picks a sensible test command and runs it.

Used by:
- `hooks/stop.py` for auto-verify-on-Stop (its primary caller)
- The `verifier` subagent's gate suite (callable as part of /ucw ship)
- Direct invocation: `ucw-verify.py` from any project root

Strategy (first match wins):
1. `.ucw/knowledge/PREFERENCES.md` → `test_runner` field (explicit override)
2. `make test` if a Makefile has a `test` target
3. Auto-detect via bin/ucw-detect-stack.py:
     pytest    → `pytest -q`
     vitest    → `vitest run --reporter=dot`
     jest      → `jest --silent`
     go test   → `go test ./...`
     cargo     → `cargo test --quiet`
     mocha     → `npx mocha`
4. Else: skip verify (return passed=True with note)

Behavior:
- Runs with a wall-clock timeout (default 60s, env UCW_VERIFY_TIMEOUT overrides)
- Captures combined stdout+stderr (truncated to 2 KB for the report)
- Returns JSON on stdout: {passed: bool, command: str, exit_code: int|null,
                            elapsed_ms: int, timed_out: bool, summary: str}
- Exits 0 if passed, 1 if failed, 2 on timeout, 3 if no runner could be selected
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Per-runner command + selector for the auto-detect path.
RUNNER_COMMANDS: dict[str, list[str]] = {
    "pytest":      ["pytest", "-q", "--no-header"],
    "vitest":      ["vitest", "run", "--reporter=dot"],
    "jest":        ["jest", "--silent"],
    "mocha":       ["npx", "mocha"],
    "playwright":  ["npx", "playwright", "test", "--reporter=dot"],
    "go test":     ["go", "test", "./..."],
    "cargo test":  ["cargo", "test", "--quiet"],
    "rspec":       ["rspec", "--format", "progress"],
}


# ---- detection -------------------------------------------------------------

def _preferences_runner(project_root: Path) -> str | None:
    prefs = project_root / ".ucw" / "knowledge" / "PREFERENCES.md"
    if not prefs.exists():
        return None
    try:
        text = prefs.read_text(encoding="utf-8")
    except OSError:
        return None
    # Look for a line like `| test_runner | pytest |` or `test_runner: pytest`
    m = re.search(r"test_runner\s*[|:=]\s*(\S+)", text, re.IGNORECASE)
    if not m:
        return None
    value = m.group(1).strip("`*_ ")
    return value.lower() if value else None


def _has_makefile_test_target(project_root: Path) -> bool:
    mk = project_root / "Makefile"
    if not mk.exists():
        return False
    try:
        text = mk.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"^test\s*:", text, re.MULTILINE))


def _detect_from_stack(project_root: Path) -> str | None:
    """Run ucw-detect-stack.py and pick the first test runner it finds."""
    helper = REPO_ROOT / "bin" / "ucw-detect-stack.py"
    if not helper.exists():
        # Try the installed location
        for cand in [
            Path.home() / ".claude" / "ucw" / "bin" / "ucw-detect-stack.py",
        ]:
            if cand.exists():
                helper = cand
                break
        else:
            return None
    try:
        cp = subprocess.run(
            [sys.executable, str(helper), str(project_root)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return None
    runners = data.get("test_runners") or []
    if not runners:
        return None
    # Prefer pytest > vitest > others (deterministic ordering)
    priority = ["pytest", "vitest", "jest", "go", "cargo", "mocha", "rspec"]
    for p in priority:
        for r in runners:
            if p in r:
                return r
    return runners[0]


def select_command(project_root: Path) -> tuple[list[str], str] | None:
    """Return (command, source) or None. `source` explains how we picked."""
    runner = _preferences_runner(project_root)
    if runner and runner in RUNNER_COMMANDS:
        return RUNNER_COMMANDS[runner], f"preferences:{runner}"

    if _has_makefile_test_target(project_root):
        return ["make", "test"], "makefile:test"

    runner = _detect_from_stack(project_root)
    if runner and runner in RUNNER_COMMANDS:
        return RUNNER_COMMANDS[runner], f"detected:{runner}"

    return None


# ---- runner ---------------------------------------------------------------

def run_verification(
    project_root: Path,
    *,
    timeout: int,
    command: list[str] | None = None,
) -> dict[str, Any]:
    if command is None:
        selected = select_command(project_root)
        if selected is None:
            return {
                "passed": True,
                "skipped": True,
                "command": None,
                "exit_code": None,
                "elapsed_ms": 0,
                "timed_out": False,
                "summary": "no test runner detected — verify skipped (configure test_runner in .ucw/knowledge/PREFERENCES.md to enable)",
                "source": "skipped",
            }
        command, source = selected
    else:
        source = "explicit"

    # If the runner binary isn't on PATH, surface a clear message instead
    # of a cryptic subprocess error.
    bin_name = command[0]
    if not shutil.which(bin_name):
        return {
            "passed": False,
            "command": " ".join(command),
            "exit_code": None,
            "elapsed_ms": 0,
            "timed_out": False,
            "summary": f"test runner not found on PATH: {bin_name!r}",
            "source": source,
        }

    start = time.perf_counter()
    try:
        cp = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "passed": False,
            "command": " ".join(command),
            "exit_code": None,
            "elapsed_ms": elapsed_ms,
            "timed_out": True,
            "summary": f"timed out after {timeout}s — set UCW_VERIFY_TIMEOUT or skip via UCW_SKIP_AUTO_VERIFY=1",
            "source": source,
        }

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    combined = (cp.stdout or "") + (cp.stderr or "")
    tail = combined.strip()[-2000:]  # truncate to last 2KB for the report
    return {
        "passed": cp.returncode == 0,
        "command": " ".join(command),
        "exit_code": cp.returncode,
        "elapsed_ms": elapsed_ms,
        "timed_out": False,
        "summary": tail or ("(no output)" if cp.returncode == 0 else "non-zero exit, no output"),
        "source": source,
    }


# ---- CLI ------------------------------------------------------------------

def _default_timeout() -> int:
    raw = os.environ.get("UCW_VERIFY_TIMEOUT")
    if raw and raw.isdigit():
        return int(raw)
    return 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ucw-verify",
        description="Run project-appropriate tests with a timeout, report JSON.",
    )
    parser.add_argument("--repo", default=os.getcwd(),
                        help="project root (default: cwd)")
    parser.add_argument("--timeout", type=int, default=_default_timeout(),
                        help="wall-clock timeout in seconds (default: 60, env UCW_VERIFY_TIMEOUT)")
    parser.add_argument("--command", help="explicit verify command (overrides detection)")
    parser.add_argument("--json", action="store_true", help="emit JSON (default behavior)")
    args = parser.parse_args(argv)

    project_root = Path(args.repo).resolve()
    command = args.command.split() if args.command else None

    result = run_verification(project_root, timeout=args.timeout, command=command)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if result["timed_out"]:
        return 2
    if result["command"] is None and result["passed"]:
        return 3  # nothing to verify — exit code 3 lets callers know
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
