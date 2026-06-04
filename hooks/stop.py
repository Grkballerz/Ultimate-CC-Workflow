#!/usr/bin/env python3
"""Stop hook — fires when Claude finishes a turn.

Four jobs:
1. **Memory distillation**: queue the transcript for distillation on SessionEnd.
2. **Auto-verify-on-Stop**: if `.ucw/state/phase == build` and streak > 0,
   shell out to `bin/ucw-verify.py` and:
     - on pass → clear the streak, set phase=verify, allow Stop
     - on fail → block with the failure summary in `reason`
     - on timeout → block with hint to set UCW_VERIFY_TIMEOUT or skip
3. **Auto-mode retry + ship** (PR B):
     - level >= 2 + verify fail: bump retry counter, block with retry
       message until cap exhausted, then fall back to hard block.
     - level >= 3 + verify pass: block with "running /ucw ship now" so the
       agent immediately commits + pushes without manual review. The
       "block" is the carrier for the instruction — without it the agent
       would Stop and wait for the next prompt.
4. **Skip override**: `UCW_SKIP_AUTO_VERIFY=1` falls back to the old blocking
   behavior (useful when tests are very slow or need orchestration the hook
   can't do — e.g. docker-compose up first).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import (
    auto_mode_level,
    auto_retry_cap,
    log,
    project_root,
    read_payload,
    state_file,
    write_output,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _current_phase(payload: dict) -> str | None:
    sf = state_file(payload, "phase")
    if not sf.exists():
        return None
    try:
        return sf.read_text().strip() or None
    except OSError:
        return None


def _edit_streak(payload: dict) -> int:
    sf = state_file(payload, "edit-streak")
    if not sf.exists():
        return 0
    try:
        return int(sf.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def _clear_streak(payload: dict) -> None:
    sf = state_file(payload, "edit-streak")
    if sf.exists():
        try:
            sf.unlink()
        except OSError:
            pass


def _set_phase(payload: dict, phase: str) -> None:
    sf = state_file(payload, "phase")
    sf.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write_text(phase + "\n", encoding="utf-8")
    except OSError:
        pass


def _retry_count(payload: dict) -> int:
    sf = state_file(payload, "auto-retries")
    if not sf.exists():
        return 0
    try:
        return int(sf.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def _bump_retry_count(payload: dict) -> int:
    n = _retry_count(payload) + 1
    sf = state_file(payload, "auto-retries")
    sf.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write_text(str(n), encoding="utf-8")
    except OSError:
        pass
    return n


def _reset_retry_count(payload: dict) -> None:
    sf = state_file(payload, "auto-retries")
    if sf.exists():
        try:
            sf.unlink()
        except OSError:
            pass


def _queue_distill(payload: dict) -> None:
    """Append a marker file the distiller picks up on next SessionEnd."""
    queue = state_file(payload, "distill-queue")
    queue.parent.mkdir(parents=True, exist_ok=True)
    transcript = payload.get("transcript_path", "")
    session = payload.get("session_id", "")
    try:
        with queue.open("a", encoding="utf-8") as fh:
            fh.write(f"{int(time.time())}\t{session}\t{transcript}\n")
    except OSError:
        pass


def _verify_binary() -> Path | None:
    """Find ucw-verify.py — repo-relative first, then installed location."""
    candidates = [
        REPO_ROOT / "bin" / "ucw-verify.py",
        Path.home() / ".claude" / "ucw" / "bin" / "ucw-verify.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _run_auto_verify(payload: dict) -> dict | None:
    """Returns the JSON result from ucw-verify.py, or None if it couldn't run."""
    verify = _verify_binary()
    if verify is None:
        log(payload, "auto-verify: ucw-verify.py not found, falling back to block")
        return None
    project = project_root(payload)
    # Inherit UCW_VERIFY_TIMEOUT from the hook env; ucw-verify reads it itself
    try:
        cp = subprocess.run(
            [sys.executable, str(verify), "--repo", str(project)],
            capture_output=True, text=True, timeout=300,  # outer cap
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


def main() -> int:
    payload = read_payload()
    phase = _current_phase(payload)
    streak = _edit_streak(payload)

    _queue_distill(payload)

    if phase != "build" or streak <= 0:
        return 0  # nothing to gate on

    # Honor the explicit skip flag — fall back to the old blocking behavior.
    if os.environ.get("UCW_SKIP_AUTO_VERIFY", "").strip() in {"1", "true", "yes"}:
        log(payload, f"Stop blocked (auto-verify skipped via env): streak={streak}")
        write_output({
            "decision": "block",
            "reason": (
                f"UCW: {streak} unverified edits in Build phase. "
                f"UCW_SKIP_AUTO_VERIFY is set, so auto-verify was skipped. "
                f"Run `/ucw ship` (or unset UCW_SKIP_AUTO_VERIFY) to proceed."
            ),
        })
        return 0

    result = _run_auto_verify(payload)
    if result is None:
        # Couldn't run the verifier at all — fall back to OLD behavior so the
        # user isn't silently let through when verification is impossible.
        log(payload, f"Stop blocked (auto-verify unavailable): streak={streak}")
        write_output({
            "decision": "block",
            "reason": (
                f"UCW: {streak} unverified edits and auto-verify couldn't run "
                f"(bin/ucw-verify.py missing or failed). Run `/ucw ship` manually."
            ),
        })
        return 0

    gates_summary = ",".join(
        f"{g.get('name')}={'pass' if g.get('passed') else 'FAIL'}"
        for g in (result.get("gates") or [])
    ) or "<single>"
    log(payload,
        f"auto-verify: passed={result.get('passed')} skipped={result.get('skipped', False)} "
        f"gates=[{gates_summary}] failed_gate={result.get('failed_gate')!r} "
        f"command={result.get('command')!r} elapsed={result.get('elapsed_ms')}ms "
        f"source={result.get('source')}")

    if result.get("skipped"):
        # No runner detected — blocking would be pointless friction. Allow Stop,
        # clear the streak, and surface the configuration hint via the log.
        # Users can fix by adding a `test_runner` line to PREFERENCES.md or a
        # `test:` target to the Makefile.
        _clear_streak(payload)
        log(payload, "auto-verify skipped (no runner) — allowing Stop")
        return 0

    level = auto_mode_level(payload)

    if result.get("passed"):
        # Tests pass → allow Stop. Clear streak + reset retries so we don't
        # re-trigger and advance phase to verify (next /ucw ship picks up at
        # the right place).
        _clear_streak(payload)
        _reset_retry_count(payload)
        _set_phase(payload, "verify")

        if level >= 3:
            # Level 3 auto-ship: don't just allow Stop — block with a nudge
            # so the agent immediately runs /ucw ship (which is also in
            # auto-mode and will skip the user-confirm gate). The "block"
            # is the carrier for the instruction; without it the agent
            # would happily Stop and wait for the next prompt.
            log(payload, f"auto-mode level {level}: verify passed → nudging /ucw ship")
            write_output({
                "decision": "block",
                "reason": (
                    f"UCW AUTO (level {level}): verify passed. Running "
                    f"`/ucw ship` now to commit and push. "
                    f"(Disable with `/ucw auto off` or UCW_AUTO_MODE=off.)"
                ),
            })
        return 0

    # A gate failed (or timed out). Surface WHICH gate so the agent doesn't
    # waste a retry hunting for failures in the wrong place.
    cmd = result.get("command") or "<no command>"
    elapsed = result.get("elapsed_ms", 0)
    summary = (result.get("summary") or "").strip()
    gate = result.get("failed_gate") or "verify"
    gate_label = f"{gate} gate" if gate in {"lint", "types", "tests"} else gate

    if level >= 2:
        # Level 2 retry loop: bump counter, decide whether to keep trying.
        cap = auto_retry_cap(payload)
        n = _bump_retry_count(payload)
        log(payload, f"auto-mode level {level}: {gate_label} FAILED, retry {n}/{cap}")
        if n >= cap:
            # Cap exhausted — fall back to a hard block. Reset the counter
            # so the next session starts fresh once the human fixes it.
            _reset_retry_count(payload)
            excerpt = summary[-800:] if summary else "(no output captured)"
            reason = (
                f"UCW AUTO (level {level}): retry cap {cap} exhausted on "
                f"{gate_label} `{cmd}` (exit {result.get('exit_code')}, {elapsed}ms). "
                f"Fix manually, then re-enable with `/ucw auto on` if "
                f"you want autonomy back. To raise the cap: "
                f"`/ucw auto on {level} --retry-cap N` or UCW_AUTO_RETRY_CAP=N.\n\n"
                f"{excerpt}"
            )
            write_output({"decision": "block", "reason": reason})
            return 0
        # Within budget — block with the failure so the agent retries.
        if result.get("timed_out"):
            reason = (
                f"UCW AUTO (retry {n}/{cap}): {gate_label} `{cmd}` TIMED OUT after "
                f"{elapsed}ms. Fix the slowness or raise UCW_VERIFY_TIMEOUT, "
                f"then continue."
            )
        else:
            excerpt = summary[-1000:] if summary else "(no output captured)"
            reason = (
                f"UCW AUTO (retry {n}/{cap}): {gate_label} `{cmd}` failed "
                f"(exit {result.get('exit_code')}, {elapsed}ms). Fix the "
                f"failures below and continue — auto-mode will keep "
                f"verifying until all gates pass or the cap is hit.\n\n"
                f"{excerpt}"
            )
        write_output({"decision": "block", "reason": reason})
        return 0

    # Level 0 / 1 — existing hard-block behavior on failure.
    if result.get("timed_out"):
        reason = (
            f"UCW auto-verify: {gate_label} `{cmd}` TIMED OUT after {elapsed}ms. "
            f"Either fix the slowness, set UCW_VERIFY_TIMEOUT=<seconds>, "
            f"or set UCW_SKIP_AUTO_VERIFY=1 and run `/ucw ship` manually."
        )
    else:
        excerpt = summary[-1200:] if summary else "(no output captured)"
        reason = (
            f"UCW auto-verify FAILED — {gate_label} `{cmd}` "
            f"(exit {result.get('exit_code')}, {elapsed}ms). "
            f"Fix the failures below, then Stop will pass.\n\n"
            f"{excerpt}"
        )
    write_output({"decision": "block", "reason": reason})
    return 0


if __name__ == "__main__":
    sys.exit(main())
