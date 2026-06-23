#!/usr/bin/env python3
"""PostToolBatch hook — the streak breaker.

After 5 consecutive Edit/Write calls with no verification gate run, the
hook auto-runs the fast gates itself (lint + types via
`bin/ucw-verify.py --gates lint,types`):

- If the gates pass → silently reset the streak and let the agent
  continue. The full suite still runs at Stop.
- If a gate fails → block with the failure summary so the agent fixes
  the right thing immediately. Counts the failure-and-fix as the
  verification, so the agent doesn't have to also run vitest just to
  reset the counter.
- If the verifier can't run (missing, unreadable output) or no runners
  detected → fall back to "agent, please run something" block — same
  behavior as before this PR for edge cases.

Tests are NOT in the streak-break gate set (full suite is reserved for
Stop) to keep PostToolBatch responsive: typecheck/lint run in seconds,
a full vitest/pytest suite can stretch into minutes and would freeze
the loop every five edits.

State lives in `.ucw/state/edit-streak` (incremented by post-tool-use.py,
reset here when a verify-gate invocation is detected in the batch OR
when the auto-run gates pass).

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

A failure the agent can't fix (pre-existing lint, tool-version mismatch, an
error in a file the diff never touched) would otherwise re-block every 5 edits
forever. The shared no-progress circuit breaker (see `_hook_common`) releases
the streak after the SAME failure repeats `UCW_VERIFY_BREAK_AFTER` times
(default 3) and records it to `.ucw/state/stuck-verify.md`.

Env toggles:
- UCW_AUTO_STREAK_VERIFY=0  → disable auto-verify, fall back to the
                              old "agent must run something" block
- UCW_STREAK_GATES=lint,types,tests  → override which gates run at the
                                        streak break (default: lint,types)
- UCW_VERIFY_BREAK_AFTER=N  → identical failures before the breaker releases
                              (default 3)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import (
    auto_mode_level,
    break_after,
    failure_signature,
    log,
    project_root,
    read_payload,
    record_failure_signature,
    reset_failure_signature,
    state_file,
    ucw_dir,
    write_output,
    write_stuck_verify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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


def _verify_binary() -> Path | None:
    """Find ucw-verify.py — repo-relative first, then installed location.
    Mirrors stop.py:_verify_binary so the two hooks stay consistent."""
    candidates = [
        REPO_ROOT / "bin" / "ucw-verify.py",
        Path.home() / ".claude" / "ucw" / "bin" / "ucw-verify.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _streak_gates() -> list[str]:
    """Which gates to run at the streak break. Default: lint + types (fast).
    Tests are reserved for Stop because a full suite can take minutes — we
    don't want PostToolBatch to freeze the loop every 5 edits.
    """
    raw = os.environ.get("UCW_STREAK_GATES", "").strip()
    if not raw:
        return ["lint", "types"]
    gates = [g.strip().lower() for g in raw.split(",") if g.strip()]
    valid = [g for g in gates if g in {"lint", "types", "tests"}]
    return valid or ["lint", "types"]


def _auto_verify_disabled() -> bool:
    return os.environ.get("UCW_AUTO_STREAK_VERIFY", "").strip() in {"0", "false", "no", "off"}


def _run_auto_verify(payload: dict, gates: list[str]) -> dict | None:
    """Returns the JSON result from ucw-verify.py, or None if it couldn't run.
    Same shape as stop.py:_run_auto_verify (which it intentionally mirrors)."""
    verify = _verify_binary()
    if verify is None:
        log(payload, "auto-verify: ucw-verify.py not found")
        return None
    project = project_root(payload)
    cmd = [sys.executable, str(verify), "--repo", str(project),
           "--gates", ",".join(gates)]
    # Auto-mode (level >= 2): if a gate's tooling isn't installed, let verify
    # install deps once and retry instead of skipping. Off auto, missing
    # tooling is skipped with a hint (verify's default).
    if auto_mode_level(payload) >= 2:
        cmd.append("--auto-install")
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300,
            env=os.environ.copy(),
        )
    except subprocess.SubprocessError as exc:
        log(payload, f"auto-verify: subprocess failed: {exc}")
        return None
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        log(payload, f"auto-verify: unparseable output: {cp.stdout[:200]}")
        return None


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

    # If the user has explicitly disabled auto-verify, restore the old
    # "agent must run something" behavior.
    if _auto_verify_disabled():
        _block_with_manual_prompt(streak_before)
        return 0

    gates = _streak_gates()
    result = _run_auto_verify(payload, gates)

    # Verifier couldn't run (missing, crashed, unparseable). Fall back to
    # asking the agent to do it — same as pre-PR-H behavior.
    if result is None:
        log(payload, "auto-verify: unavailable, falling back to manual prompt")
        _block_with_manual_prompt(streak_before, gates=gates)
        return 0

    gates_summary = ",".join(
        f"{g.get('name')}={'pass' if g.get('passed') else 'FAIL'}"
        for g in (result.get("gates") or [])
    ) or "<none>"
    log(payload,
        f"auto-verify (streak-break): passed={result.get('passed')} "
        f"skipped={result.get('skipped', False)} gates=[{gates_summary}] "
        f"failed_gate={result.get('failed_gate')!r}")

    # No runners detected for the requested gates → nothing to verify, reset
    # the streak and move on. Otherwise the agent's stuck in a loop with
    # nothing it can run.
    if result.get("skipped"):
        _reset_streak(payload)
        reset_failure_signature(payload, "batch")
        return 0

    if result.get("passed"):
        _reset_streak(payload)
        reset_failure_signature(payload, "batch")
        # If a gate was skipped because its tooling isn't installed, surface a
        # NON-blocking hint so the agent knows the gate didn't really run (and
        # how to enable it) — but don't trap it. The Stop hook still runs the
        # full suite before the turn ends.
        hint = result.get("setup_hint")
        if hint:
            log(payload, f"setup-skip: {result.get('setup_skipped')}")
            write_output({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolBatch",
                    "additionalContext": f"⚠️ {hint}",
                }
            })
        return 0

    # A gate failed. Block with the failure so the agent fixes it. Don't
    # reset the streak — but the fix-and-rerun cycle will reset on the
    # next batch (the agent's verify run satisfies _batch_ran_verification).
    gate = result.get("failed_gate") or "verify"
    gate_label = f"{gate} gate" if gate in {"lint", "types", "tests"} else gate
    cmd = result.get("command") or "<no command>"
    elapsed = result.get("elapsed_ms", 0)
    summary = (result.get("summary") or "").strip()

    # No-progress circuit breaker: if this exact gate failure has repeated
    # unchanged too many times, the streak-break loop is trapping the agent on
    # something it can't fix (pre-existing / out-of-scope). Release the streak
    # so it can continue (the Stop hook is the final gate anyway), and record
    # the stuck failure for the human.
    cap_breaker = break_after()
    fail_count = record_failure_signature(payload, "batch", failure_signature(result))
    if fail_count >= cap_breaker:
        write_stuck_verify(payload, "batch", result, fail_count)
        reset_failure_signature(payload, "batch")
        _reset_streak(payload)
        log(payload,
            f"circuit breaker released streak-break after {fail_count} identical "
            f"{gate_label} failures — recorded to .ucw/state/stuck-verify.md")
        return 0

    excerpt = summary[-1000:] if summary else "(no output captured)"
    reason = (
        f"UCW streak-break auto-verify: {gate_label} `{cmd}` failed "
        f"(exit {result.get('exit_code')}, {elapsed}ms). "
        f"Fix the failures below before continuing — the next edit batch "
        f"that runs verify will reset the streak. (If you can't fix it — "
        f"pre-existing or out of scope — this auto-releases after "
        f"{cap_breaker} identical tries: {fail_count}/{cap_breaker}.)\n\n"
        f"{excerpt}"
    )
    write_output({
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": (
                f"Streak={streak_before}, {gate_label} failed. Fix the issue, "
                f"then run any verify gate to reset the counter."
            ),
        },
    })
    return 0


def _block_with_manual_prompt(streak: int, *, gates: list[str] | None = None) -> None:
    """Old behavior: block and tell the agent to run a verify gate itself.
    Used when auto-verify is disabled or unavailable."""
    gates_hint = ",".join(gates) if gates else "lint,types"
    write_output({
        "decision": "block",
        "reason": (
            f"UCW streak breaker: {streak} edits without running a verify "
            f"gate (lint, types, or tests). Run any of them — `eslint`, `tsc`, "
            f"`pytest`, `make lint`, `bin/ucw-verify.py --gates {gates_hint}`, "
            f"etc. — before continuing. Untested edit chains lead to "
            f"regression debt."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": (
                f"Edit streak = {streak}. Run a verify gate now (lint, "
                f"types, or tests — `bin/ucw-verify.py --gates {gates_hint}` "
                f"runs them in one shot). After it passes, the streak "
                f"resets and you can continue."
            ),
        },
    })


if __name__ == "__main__":
    sys.exit(main())
