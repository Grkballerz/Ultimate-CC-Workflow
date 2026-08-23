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
5. **Strict gates (auto level >= 2)**: with auto-mode driving there is no
   human in the loop to notice a silently skipped gate, so a verify result
   that skipped everything (no runner detected) or setup-skipped a gate
   (tool missing) is NOT streak-clearing success — it blocks with an install
   hint. ucw-verify.py itself fails missing-tool gates under the same
   conditions; the checks here are belt-and-braces for older installed
   verifiers. A passing run also records the tree-keyed cache entry
   (.ucw/state/last-verify.json, written by ucw-verify.py), so the /ucw ship
   that follows a passing Stop-hook verify is a cache hit.
6. **No-progress circuit breaker (level 0/1 only)**: if the SAME failure
   repeats unchanged `UCW_VERIFY_BREAK_AFTER` times (default 3), release the
   Stop block instead of trapping the agent. A failure the diff can't fix
   (pre-existing lint, tool-version mismatch, untouched-file error) would
   otherwise loop forever: the agent can't Stop, and each fix attempt re-arms
   the streak. On release we record the stuck failure to
   `.ucw/state/stuck-verify.md` for the human. Level >= 2 is excluded — it has
   its own retry-cap governor that intentionally hard-blocks for a human at
   cap exhaustion (the autonomy contract).
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
    break_after,
    failure_signature,
    log,
    project_root,
    read_payload,
    record_failure_signature,
    reset_failure_signature,
    state_file,
    write_output,
    write_stuck_verify,
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
    cmd = [sys.executable, str(verify), "--repo", str(project)]
    # Auto-mode (level >= 2): install missing tooling once and retry rather
    # than skipping the gate. Off auto, verify setup-skips it with a hint.
    if auto_mode_level(payload) >= 2:
        cmd.append("--auto-install")
    try:
        cp = subprocess.run(
            cmd,
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

    level = auto_mode_level(payload)
    # Strict gates: with auto-mode driving (level >= 2) a skip is not success —
    # nobody is left in the loop to notice it. (Phase verify/land also implies
    # strict, but this hook only gates in build, so level is the trigger here.)
    strict = level >= 2

    if result.get("skipped"):
        if strict:
            log(payload,
                f"auto-verify skipped (no runner) under strict gates (level {level}) — blocking")
            write_output({
                "decision": "block",
                "reason": (
                    f"UCW AUTO (level {level}): verify could not run ANY gate — "
                    f"tool missing — install or run install.sh --reinstall-deps, "
                    f"or configure a runner (test_runner in "
                    f".ucw/knowledge/PREFERENCES.md, a Makefile target, or "
                    f"package.json scripts). Strict gates refuse to count a "
                    f"skipped suite as a pass."
                ),
            })
            return 0
        # No runner detected — blocking would be pointless friction. Allow Stop,
        # clear the streak, and surface the configuration hint via the log.
        # Users can fix by adding a `test_runner` line to PREFERENCES.md or a
        # `test:` target to the Makefile.
        _clear_streak(payload)
        reset_failure_signature(payload, "stop")
        log(payload, "auto-verify skipped (no runner) — allowing Stop")
        return 0

    if result.get("passed") and strict and result.get("setup_skipped"):
        # Belt-and-braces: a current ucw-verify.py already FAILS missing-tool
        # gates under strict conditions, but an older installed copy may still
        # report them as a setup-skipped "pass". Never let that clear the
        # streak when auto-mode is driving.
        gates = ", ".join(result.get("setup_skipped") or [])
        log(payload,
            f"auto-verify setup-skipped [{gates}] under strict gates (level {level}) — blocking")
        write_output({
            "decision": "block",
            "reason": (
                f"UCW AUTO (level {level}): gate(s) [{gates}] were skipped: "
                f"tool missing — install or run install.sh --reinstall-deps. "
                f"Strict gates treat a missing tool as a failure, not a skip."
            ),
        })
        return 0

    if result.get("passed"):
        # Tests pass → allow Stop. Clear streak + reset retries so we don't
        # re-trigger and advance phase to verify (next /ucw ship picks up at
        # the right place).
        _clear_streak(payload)
        _reset_retry_count(payload)
        reset_failure_signature(payload, "stop")
        _set_phase(payload, "verify")

        if result.get("setup_hint"):
            # A gate couldn't run because its tooling isn't installed; we didn't
            # block on it. Record why so it's not mistaken for a clean pass.
            log(payload, f"setup-skip ({result.get('setup_skipped')}): {result.get('setup_hint')}")

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

    cap_breaker = break_after()

    if level >= 2:
        # Autonomous mode (level >= 2) has its OWN governor: the retry loop
        # keeps the agent fixing until the cap, then hard-blocks for a human.
        # That's the intended autonomy contract — we don't apply the
        # no-progress breaker here (it would release silently when the user
        # explicitly asked to be pulled in at cap exhaustion). The env escapes
        # below still apply.
        escape = (
            " [Escape: drop the gate with UCW_VERIFY_GATES=tests, or set "
            "UCW_SKIP_AUTO_VERIFY=1.]"
        )
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
            write_output({"decision": "block", "reason": reason + escape})
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
        write_output({"decision": "block", "reason": reason + escape})
        return 0

    # Level 0 / 1 — no retry cap and (historically) no release valve, so an
    # unfixable failure (pre-existing lint, tool-version mismatch, an error in
    # an untouched file) trapped the agent forever: it couldn't Stop, and each
    # fix attempt re-armed the streak. The no-progress circuit breaker releases
    # the Stop block once the SAME failure has repeated unchanged too many
    # times, recording it for the human instead of looping.
    fail_count = record_failure_signature(payload, "stop", failure_signature(result))
    if fail_count >= cap_breaker:
        write_stuck_verify(payload, "stop", result, fail_count)
        reset_failure_signature(payload, "stop")
        _clear_streak(payload)
        _reset_retry_count(payload)
        log(payload,
            f"circuit breaker released Stop after {fail_count} identical "
            f"{gate_label} failures — recorded to .ucw/state/stuck-verify.md")
        return 0

    # Escape-hatch hint appended to every block reason so the agent (and human)
    # can always find the release valve — not just on the timeout path.
    escape = (
        f" [Escape: this {gate_label} failure auto-releases after {cap_breaker} "
        f"identical tries ({fail_count}/{cap_breaker}); or drop the gate with "
        f"UCW_VERIFY_GATES=tests, or set UCW_SKIP_AUTO_VERIFY=1.]"
    )
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
    write_output({"decision": "block", "reason": reason + escape})
    return 0


if __name__ == "__main__":
    sys.exit(main())
