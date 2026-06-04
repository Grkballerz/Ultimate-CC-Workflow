#!/usr/bin/env python3
"""PostToolBatch hook — the streak breaker.

After 5 consecutive Edit/Write calls with no verification gate run, block
the agentic loop from continuing until the agent runs lint, types, OR
tests (any verification step counts). Inspired by ECC's guardrails.

State lives in `.ucw/state/edit-streak` (incremented by post-tool-use.py,
reset here when a verify-gate invocation is detected).

Detection notes:
- Claude Code's PostToolBatch payload shape isn't formally documented and
  has shifted across versions (sometimes `tools`, sometimes `batch`,
  sometimes nested under `tool_results`). Rather than hardcode one shape,
  we recursively scan every string value in the payload for known verify
  commands with word-boundary matching — so `pytest -q` counts but
  `tests/test_pytest.py` does not.
- "Verify" is broader than just tests: a `tsc --noEmit` or `eslint .` run
  is a real check on the agent's edits and resets the streak too. This
  matches what `bin/ucw-verify.py` runs as gates and what auto-mode
  retries on. Otherwise the agent gets nagged to run vitest even when a
  typecheck is the more relevant check.
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

# Word-boundary patterns for each verify gate. `\b` keeps `tests/` paths
# and variable names like `pytest_plugins` from triggering false positives.
# Two-word patterns (`go test`, `cargo check`) avoid matching bare words
# like `test`/`go`/`cargo`/`check` in unrelated contexts.
_VERIFY_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ---- Test runners (gate: tests) ----
    re.compile(r"\bpytest\b"),
    re.compile(r"\bvitest\b"),
    re.compile(r"\bjest\b"),
    re.compile(r"\bmocha\b"),
    re.compile(r"\brspec\b"),
    re.compile(r"\bplaywright\s+test\b"),
    re.compile(r"\bgo\s+test\b"),
    re.compile(r"\bcargo\s+test\b"),
    re.compile(r"\bphpunit\b"),
    # ---- Linters / formatters (gate: lint) ----
    re.compile(r"\beslint\b"),
    re.compile(r"\bbiome\s+(?:check|lint|ci|format)\b"),
    re.compile(r"\bruff\s+(?:check|format)\b"),
    re.compile(r"\bgolangci-lint\b"),
    re.compile(r"\bcargo\s+clippy\b"),
    # ---- Type checkers (gate: types) ----
    re.compile(r"\btsc\b"),
    re.compile(r"\bmypy\b"),
    re.compile(r"\bcargo\s+check\b"),
    re.compile(r"\bgo\s+vet\b"),
    # ---- Generic build-system invocations of any gate ----
    # `make test|lint|typecheck|types|check|verify` — covers all the
    # Makefile-driven gates ucw-verify.py looks for.
    re.compile(r"\bmake\s+(?:test|lint|typecheck|types|check|verify)\b"),
    # `npm|pnpm|yarn|bun` running a verify-looking script. We deliberately
    # do NOT match arbitrary scripts (e.g. `pnpm run dev`, `pnpm run build`)
    # because those don't verify anything.
    re.compile(
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+|exec\s+)?"
        r"(?:test|lint|typecheck|tsc|check|verify|format)\b"
    ),
    # ---- UCW's own verifier ----
    # Direct invocations like `bin/ucw-verify.py --gates lint,types` should
    # also count — that IS verification.
    re.compile(r"\bucw-verify(?:\.py)?\b"),
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


def _looks_like_verify_command(s: str) -> bool:
    return any(p.search(s) for p in _VERIFY_SIGNAL_PATTERNS)


def _scan_for_verify_command(node: Any, *, parent_key: str = "") -> bool:
    """Walk an arbitrary JSON-shaped payload looking for a verify-gate command.

    We don't pin down the exact shape because Claude Code's PostToolBatch
    payload is not stably documented; instead we look at every leaf string
    that could plausibly be a shell command, skipping fields we know are
    paths.
    """
    if isinstance(node, str):
        if parent_key in _PATH_LIKE_KEYS:
            return False
        return _looks_like_verify_command(node)
    if isinstance(node, dict):
        for k, v in node.items():
            if _scan_for_verify_command(v, parent_key=k):
                return True
        return False
    if isinstance(node, list):
        return any(_scan_for_verify_command(item, parent_key=parent_key) for item in node)
    return False


def _batch_ran_verification(payload: dict) -> bool:
    """Inspect the batch payload for any tool call that runs a verify gate
    (lint, types, OR tests). Any of the three resets the streak."""
    return _scan_for_verify_command(payload)


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

    detected = _batch_ran_verification(payload)
    streak_before = _streak(payload)
    log(payload,
        f"detection={detected} streak={streak_before} batch_keys={sorted(payload.keys())}")

    if detected:
        _reset_streak(payload)
        return 0

    if streak_before < STREAK_THRESHOLD:
        return 0

    log(payload, f"streak breaker fired: {streak_before} edits without a verify gate")
    write_output({
        "decision": "block",
        "reason": (
            f"UCW streak breaker: {streak_before} edits without running a verify "
            f"gate (lint, types, or tests). Run any of them — `eslint`, `tsc`, "
            f"`pytest`, `make lint`, `bin/ucw-verify.py`, etc. — before continuing. "
            f"Untested edit chains lead to regression debt."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": (
                f"Edit streak = {streak_before}. Run a verify gate now (lint, "
                f"types, or tests — `bin/ucw-verify.py` runs them all). After "
                f"it passes, the streak resets and you can continue."
            ),
        },
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
