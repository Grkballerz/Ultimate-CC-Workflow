#!/usr/bin/env python3
"""PostToolBatch hook — the streak breaker.

After 5 consecutive Edit/Write calls with no Bash test invocation, block the
agentic loop from continuing until a test is run. Inspired by ECC's
guardrails.

State lives in `.ucw/state/edit-streak` (incremented by post-tool-use.py,
reset here when a test run is detected).

Detection notes:
- Claude Code's PostToolBatch payload shape isn't formally documented and
  has shifted across versions (sometimes `tools`, sometimes `batch`,
  sometimes nested under `tool_results`). Rather than hardcode one shape,
  we recursively scan every string value in the payload for known test
  runner invocations with word-boundary matching — so `pytest -q` counts
  but `tests/test_pytest.py` does not.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, state_file, ucw_dir, write_output

STREAK_THRESHOLD = 5

# Word-boundary patterns for each runner. `\b` keeps `tests/` paths and
# variable names like `pytest_plugins` from triggering a false positive.
# `go test` and `cargo test` need the two-word form to avoid matching
# the bare words `test`/`go`/`cargo` in unrelated contexts.
_TEST_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpytest\b"),
    re.compile(r"\bvitest\b"),
    re.compile(r"\bjest\b"),
    re.compile(r"\bmocha\b"),
    re.compile(r"\brspec\b"),
    re.compile(r"\bplaywright\s+test\b"),
    re.compile(r"\bgo\s+test\b"),
    re.compile(r"\bcargo\s+test\b"),
    re.compile(r"\bmake\s+test\b"),
    re.compile(r"\bnpm\s+(?:run\s+)?test\b"),
    re.compile(r"\bpnpm\s+(?:run\s+)?test\b"),
    re.compile(r"\byarn\s+(?:run\s+)?test\b"),
    re.compile(r"\bbun\s+test\b"),
    re.compile(r"\bphpunit\b"),
)

# Keys whose values are almost certainly file paths, not commands. Skipping
# them avoids false positives like `tests/test_pytest.py` triggering a match
# despite the word-boundary regex (a path can contain `\bpytest\b`).
_PATH_LIKE_KEYS = frozenset({
    "file_path", "filepath", "path", "cwd", "transcript_path",
    "session_id", "agent_id", "agent_type", "permission_mode",
    "hook_event_name",
})


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


def _looks_like_test_command(s: str) -> bool:
    return any(p.search(s) for p in _TEST_SIGNAL_PATTERNS)


def _scan_for_test_command(node: Any, *, parent_key: str = "") -> bool:
    """Walk an arbitrary JSON-shaped payload looking for a test command string.

    We don't pin down the exact shape because Claude Code's PostToolBatch
    payload is not stably documented; instead we look at every leaf string
    that could plausibly be a shell command, skipping fields we know are
    paths.
    """
    if isinstance(node, str):
        if parent_key in _PATH_LIKE_KEYS:
            return False
        return _looks_like_test_command(node)
    if isinstance(node, dict):
        for k, v in node.items():
            if _scan_for_test_command(v, parent_key=k):
                return True
        return False
    if isinstance(node, list):
        return any(_scan_for_test_command(item, parent_key=parent_key) for item in node)
    return False


def _batch_ran_tests(payload: dict) -> bool:
    """Inspect the batch payload for any tool call that looks test-y."""
    return _scan_for_test_command(payload)


def _maybe_capture_payload(payload: dict) -> None:
    """If `UCW_DEBUG_PAYLOADS=1` or `.ucw/state/debug-payloads` exists, dump
    the raw payload to `.ucw/state/post-tool-batch-payloads.jsonl` so the
    user can verify the walker actually matches what Claude Code is sending.

    Off by default — payloads can be large or include path data."""
    enabled = (
        os.environ.get("UCW_DEBUG_PAYLOADS", "").strip() in {"1", "true", "yes"}
        or (ucw_dir(payload) / "state" / "debug-payloads").exists()
    )
    if not enabled:
        return
    try:
        out = ucw_dir(payload) / "state" / "post-tool-batch-payloads.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "payload": payload}))
            fh.write("\n")
    except (OSError, TypeError):
        pass  # never let debug logging break the hook


def main() -> int:
    payload = read_payload()
    _maybe_capture_payload(payload)

    detected = _batch_ran_tests(payload)
    streak_before = _streak(payload)
    log(payload,
        f"detection={detected} streak={streak_before} batch_keys={sorted(payload.keys())}")

    if detected:
        _reset_streak(payload)
        return 0

    if streak_before < STREAK_THRESHOLD:
        return 0

    log(payload, f"streak breaker fired: {streak_before} edits without a test run")
    write_output({
        "decision": "block",
        "reason": (
            f"UCW streak breaker: {streak_before} edits without running tests. "
            f"Run the test suite (or the relevant test file) before continuing — "
            f"untested edit chains lead to regression debt."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": (
                f"Edit streak = {streak_before}. Run tests now. After tests pass, the "
                f"streak resets and you can continue."
            ),
        },
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
