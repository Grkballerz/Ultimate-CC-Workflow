#!/usr/bin/env python3
"""PostToolBatch hook — the streak breaker.

After 5 consecutive Edit/Write calls with no Bash test invocation, block the
agentic loop from continuing until a test is run. Inspired by ECC's
guardrails.

State lives in `.ucw/state/edit-streak` (incremented by post-tool-use.py,
reset here when a test run is detected).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, state_file, write_output

STREAK_THRESHOLD = 5


def _streak(payload: dict) -> int:
    sf = state_file(payload, "edit-streak")
    if not sf.exists():
        return 0
    try:
        return int(sf.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def _reset_streak(payload: dict) -> None:
    sf = state_file(payload, "edit-streak")
    if sf.exists():
        try:
            sf.write_text("0")
        except OSError:
            pass


def _batch_ran_tests(payload: dict) -> bool:
    """Inspect the batch payload for any Bash call that looks test-y."""
    tools = payload.get("tools", []) or payload.get("batch", []) or []
    test_signals = ("pytest", "vitest", "jest", "go test", "cargo test", "rspec", "mocha")
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        cmd = (entry.get("tool_input", {}) or {}).get("command", "")
        if any(sig in cmd for sig in test_signals):
            return True
    return False


def main() -> int:
    payload = read_payload()

    if _batch_ran_tests(payload):
        _reset_streak(payload)
        return 0

    streak = _streak(payload)
    if streak < STREAK_THRESHOLD:
        return 0

    log(payload, f"streak breaker fired: {streak} edits without a test run")
    write_output({
        "decision": "block",
        "reason": (
            f"UCW streak breaker: {streak} edits without running tests. "
            f"Run the test suite (or the relevant test file) before continuing — "
            f"untested edit chains lead to regression debt."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": (
                f"Edit streak = {streak}. Run tests now. After tests pass, the "
                f"streak resets and you can continue."
            ),
        },
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
