"""Tests for the hook scripts not covered in test_hooks.py.

Covers: stop, session-end (including real distillation), pre-compact,
subagent-stop, post-tool-use edge cases.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"


def _run(hook: str, payload: dict) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/memory"
    cp = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=10,
    )
    return cp.returncode, cp.stdout, cp.stderr


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    return tmp_path


# ---- stop --------------------------------------------------------------------
# Stop now auto-runs verification (bin/ucw-verify.py) in build phase with
# streak > 0. The semantics by case:
#   project has failing tests → block with summary
#   project has passing tests → clear streak, set phase=verify, allow Stop
#   project has no test runner → allow Stop (no point blocking)
#   UCW_SKIP_AUTO_VERIFY=1     → fall back to old hard block

def test_stop_blocks_with_failing_tests(project):
    """A project with a failing test runner should produce a block + summary."""
    (project / "Makefile").write_text("test:\n\t@echo 'mock failure' && false\n")
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "FAILED" in body["reason"] or "failure" in body["reason"].lower()


def test_stop_allows_with_passing_tests(project):
    """Passing tests → allow Stop, clear streak, advance phase=verify."""
    (project / "Makefile").write_text("test:\n\t@true\n")
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    assert out == "", "passing auto-verify should produce no output (Stop allowed)"
    # Streak cleared, phase advanced
    streak = project / ".ucw" / "state" / "edit-streak"
    assert not streak.exists() or streak.read_text().strip() == "0"
    phase = (project / ".ucw" / "state" / "phase").read_text().strip()
    assert phase == "verify"


def test_stop_allows_when_no_runner_detected(project):
    """No Makefile, no .ucw/knowledge/PREFERENCES.md, no detectable stack →
    auto-verify reports skipped → Stop allowed (no point blocking)."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("1")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    assert out == ""
    streak = project / ".ucw" / "state" / "edit-streak"
    assert not streak.exists() or streak.read_text().strip() == "0"


def test_stop_skip_env_falls_back_to_hard_block(project):
    """UCW_SKIP_AUTO_VERIFY=1 → restore old blocking semantics for slow / external
    verification setups."""
    (project / "Makefile").write_text("test:\n\t@true\n")
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    env = {**os.environ, "UCW_SKIP_AUTO_VERIFY": "1"}
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "stop.py")],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True, text=True, env=env, timeout=10,
    )
    body = json.loads(cp.stdout)
    assert body["decision"] == "block"
    assert "UCW_SKIP_AUTO_VERIFY" in body["reason"]


def test_stop_blocks_with_failure_summary_includes_command(project):
    """The block reason should include the test command for context."""
    (project / "Makefile").write_text("test:\n\t@false\n")
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("2")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert "make test" in body["reason"]


def test_stop_blocks_on_lint_failure_even_when_tests_pass(project):
    """The bug this fixes: previously verify only ran the test runner, so
    `eslint .` / `tsc --noEmit` failures slipped through and the hook would
    advance phase=verify on a broken codebase. Now lint runs first and
    blocks the Stop."""
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'lint problem' >&2 && false\n"
        "test:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "lint" in body["reason"].lower()
    # Phase must NOT have advanced — lint failed, build is not done.
    assert (project / ".ucw" / "state" / "phase").read_text().strip() == "build"


# ---- stop: no-progress circuit breaker --------------------------------------
# A failure the diff can't fix (pre-existing lint, tool mismatch, untouched
# file) would otherwise trap the agent forever — it can't Stop, and every fix
# attempt re-arms the streak. After UCW_VERIFY_BREAK_AFTER identical failures,
# the breaker releases the Stop block and records the failure for the human.

def _stop_with_env(project, env_extra: dict) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/memory"
    env.update(env_extra)
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "stop.py")],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True, text=True, env=env, timeout=20,
    )
    return cp.returncode, cp.stdout, cp.stderr


def test_stop_circuit_breaker_releases_after_identical_failures(project):
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'E501 line too long' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    payload = {"cwd": str(project)}

    # Default threshold is 3: first two identical failures still block.
    for _ in range(2):
        _rc, out, _ = _run("stop.py", payload)
        assert json.loads(out)["decision"] == "block"
        assert (project / ".ucw" / "state" / "edit-streak").read_text().strip() == "3"

    # Third identical failure → breaker releases: allow Stop (no block),
    # streak cleared, stuck failure recorded for the human.
    _rc, out, _ = _run("stop.py", payload)
    assert out == "", f"breaker should allow Stop silently, got: {out!r}"
    stuck = project / ".ucw" / "state" / "stuck-verify.md"
    assert stuck.exists()
    assert "E501 line too long" in stuck.read_text()
    assert not (project / ".ucw" / "state" / "edit-streak").exists(), \
        "streak must be cleared on release so the agent isn't immediately re-gated"


def test_stop_circuit_breaker_resets_on_changed_failure(project):
    """Alternating (changing) failures never count as 'no progress', so the
    breaker must not release — the agent is still moving."""
    mk = project / "Makefile"
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    payload = {"cwd": str(project)}
    for msg in ["alpha problem", "beta problem", "alpha problem", "beta problem"]:
        mk.write_text(f"lint:\n\t@echo '{msg}' >&2 && false\n")
        _rc, out, _ = _run("stop.py", payload)
        assert json.loads(out)["decision"] == "block", f"{msg!r} should still block"
    assert not (project / ".ucw" / "state" / "stuck-verify.md").exists()


def test_stop_circuit_breaker_threshold_env(project):
    """UCW_VERIFY_BREAK_AFTER=1 releases on the very first failure."""
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'unfixable' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _stop_with_env(project, {"UCW_VERIFY_BREAK_AFTER": "1"})
    assert out == "", "break-after=1 must release on the first failure"
    assert (project / ".ucw" / "state" / "stuck-verify.md").exists()


def test_stop_block_reason_advertises_escape_hatch(project):
    """Every block reason must name a way out (the auto-release or an env
    escape) so the agent and human aren't left guessing."""
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'some lint error' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    reason = json.loads(out)["reason"]
    assert "auto-releases" in reason or "UCW_SKIP_AUTO_VERIFY" in reason


def test_stop_blocks_on_typecheck_failure_even_when_tests_pass(project):
    (project / "Makefile").write_text(
        "typecheck:\n\t@echo 'TS2322' >&2 && false\n"
        "test:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("1")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "types" in body["reason"].lower()
    assert "TS2322" in body["reason"]


def test_stop_allows_only_when_all_gates_pass(project):
    """Lint + types + tests all pass → streak cleared, phase=verify."""
    (project / "Makefile").write_text(
        "lint:\n\t@true\n"
        "typecheck:\n\t@true\n"
        "test:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    assert out == ""
    assert (project / ".ucw" / "state" / "phase").read_text().strip() == "verify"


def test_stop_does_not_emit_hookSpecificOutput(project):
    """Regression: Claude Code's strict schema only allows hookSpecificOutput
    on PreToolUse / UserPromptSubmit / PostToolUse / PostToolBatch. Stop
    must never emit it, including under the new auto-verify failure path.
    """
    (project / "Makefile").write_text("test:\n\t@false\n")
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert "hookSpecificOutput" not in body


def test_stop_allows_when_phase_not_build(project):
    (project / ".ucw" / "state" / "phase").write_text("plan")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    assert out == ""


def test_stop_allows_when_streak_zero(project):
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("0")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    assert out == ""


def test_stop_queues_distill_job(project, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"message": {"content": "noise"}}))
    _run("stop.py", {
        "cwd": str(project), "session_id": "abc", "transcript_path": str(transcript)
    })
    queue = project / ".ucw" / "state" / "distill-queue"
    assert queue.exists()
    assert "abc" in queue.read_text()
    assert str(transcript) in queue.read_text()


# ---- session-end with real distillation --------------------------------------

def test_session_end_distills_queued_transcript(project, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({
        "message": {"content": "We chose to use bun because it's fast."}
    }))
    queue = project / ".ucw" / "state" / "distill-queue"
    queue.write_text(f"123\tsess1\t{transcript}\n")
    # Initialize DB so distillation has a target.
    from ucw_memory import init_db
    init_db(project / ".ucw" / "memory.sqlite")

    rc, _out, _err = _run("session-end.py", {"cwd": str(project)})
    assert rc == 0

    # Queue should be drained
    assert queue.read_text() == ""
    # Fact should be in the DB
    from ucw_memory import MemoryDB
    with MemoryDB(project / ".ucw" / "memory.sqlite") as db:
        hits = db.fts_search("bun", limit=5)
        assert any("bun" in f.object.lower() for f, _ in hits)


def test_session_end_clears_state(project):
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    _run("session-end.py", {"cwd": str(project)})
    assert not (project / ".ucw" / "state" / "phase").exists()
    assert not (project / ".ucw" / "state" / "edit-streak").exists()


def test_session_end_clears_auto_retries(project):
    """PR D fix: auto-retries counter must clear on SessionEnd so a counter
    from yesterday's failed retry loop doesn't poison today's first failure
    (cap exhausted on the first real failure)."""
    (project / ".ucw" / "state" / "auto-retries").write_text("2")
    _run("session-end.py", {"cwd": str(project)})
    assert not (project / ".ucw" / "state" / "auto-retries").exists()


def test_session_end_preserves_auto_mode_state(project):
    """Auto-mode itself is user intent — must survive across sessions."""
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 3, "since": "2026-06-01T00:00:00Z"})
    )
    _run("session-end.py", {"cwd": str(project)})
    assert (project / ".ucw" / "state" / "auto-mode").exists()


# ---- PR E: phase preservation across exit ----------------------------------

def test_session_end_preserves_phase_when_auto_on_and_plan_exists(project):
    """PR E: exiting mid-build (auto-mode on, plan persisted) is the
    'free context to resume later' case — phase MUST survive so the
    UserPromptSubmit resume hint fires on the next session's first prompt."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 3, "since": "2026-06-01T00:00:00Z"})
    )
    (project / ".ucw" / "state" / "plan.md").write_text("1. do x\n")
    _run("session-end.py", {"cwd": str(project)})
    assert (project / ".ucw" / "state" / "phase").exists(), \
        "phase must survive when auto-mode is on and plan.md exists"
    assert (project / ".ucw" / "state" / "phase").read_text().strip() == "build"


def test_session_end_clears_phase_when_auto_mode_off(project):
    """Back-compat: no auto-mode → original 'end of session = end of work'
    semantics → phase clears."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "plan.md").write_text("1. do x\n")
    # No auto-mode state
    _run("session-end.py", {"cwd": str(project)})
    assert not (project / ".ucw" / "state" / "phase").exists()


def test_session_end_clears_phase_when_no_plan(project):
    """Auto-mode on but no plan → not really mid-workflow → clear phase."""
    (project / ".ucw" / "state" / "phase").write_text("scope")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 2})
    )
    # No plan.md
    _run("session-end.py", {"cwd": str(project)})
    assert not (project / ".ucw" / "state" / "phase").exists()


def test_session_end_clears_streak_and_retries_even_when_preserving_phase(project):
    """Only phase is conditional. edit-streak and auto-retries always clear
    (streak is per-session momentum; retries reset for the next build)."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 2}))
    (project / ".ucw" / "state" / "plan.md").write_text("1. do x\n")
    (project / ".ucw" / "state" / "edit-streak").write_text("4")
    (project / ".ucw" / "state" / "auto-retries").write_text("2")
    _run("session-end.py", {"cwd": str(project)})
    assert (project / ".ucw" / "state" / "phase").exists()
    assert not (project / ".ucw" / "state" / "edit-streak").exists()
    assert not (project / ".ucw" / "state" / "auto-retries").exists()


def test_session_end_logs_phase_preservation(project):
    """Sanity: when we preserve phase, the action is logged for traceability."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 2}))
    (project / ".ucw" / "state" / "plan.md").write_text("1. do x\n")
    _run("session-end.py", {"cwd": str(project)})
    log = (project / ".ucw" / "hooks.log").read_text()
    assert "phase preserved" in log


def test_session_end_env_off_auto_mode_clears_phase(project):
    """UCW_AUTO_MODE=off env wins over state → phase clears (escape hatch)."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 3}))
    (project / ".ucw" / "state" / "plan.md").write_text("1. do x\n")
    env = {**os.environ, "UCW_AUTO_MODE": "off"}
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "session-end.py")],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert cp.returncode == 0
    assert not (project / ".ucw" / "state" / "phase").exists()


# ---- pre-compact -------------------------------------------------------------

def test_pre_compact_writes_digest(project):
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("4")
    _run("pre-compact.py", {"cwd": str(project), "trigger": "manual"})
    digest = project / ".ucw" / "state" / "pre-compact-digest.md"
    assert digest.exists()
    text = digest.read_text()
    assert "build" in text
    assert "4" in text
    assert "manual" in text


def test_pre_compact_digest_includes_auto_mode_state(project):
    """PR D: digest must capture auto-mode level + retry count so the agent
    can see them post-compact via /ucw resume or the auto-injected hint."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 3, "since": "2026-06-01T00:00:00Z"})
    )
    (project / ".ucw" / "state" / "auto-retries").write_text("1")
    _run("pre-compact.py", {"cwd": str(project), "trigger": "manual"})
    digest = project / ".ucw" / "state" / "pre-compact-digest.md"
    text = digest.read_text()
    assert "level 3" in text
    assert "retries 1/3" in text


def test_pre_compact_digest_off_when_no_auto_mode(project):
    (project / ".ucw" / "state" / "phase").write_text("build")
    _run("pre-compact.py", {"cwd": str(project), "trigger": "manual"})
    digest = project / ".ucw" / "state" / "pre-compact-digest.md"
    text = digest.read_text()
    assert "Auto-mode at compact: off" in text


def test_pre_compact_digest_notes_plan_and_spec_presence(project):
    """Digest must point at plan.md and spec.md so the agent can read them
    if it wants the full context post-compact."""
    (project / ".ucw" / "state" / "phase").write_text("plan")
    (project / ".ucw" / "state" / "plan.md").write_text("# Plan\n1. do x\n")
    (project / ".ucw" / "state" / "spec.md").write_text("# Spec\n\nbuilding x\n")
    _run("pre-compact.py", {"cwd": str(project), "trigger": "manual"})
    digest = project / ".ucw" / "state" / "pre-compact-digest.md"
    text = digest.read_text()
    assert "Plan persisted: yes" in text
    assert "Spec persisted: yes" in text
    assert "/ucw resume" in text


def test_pre_compact_digest_marks_missing_plan_and_spec(project):
    """Make missing state explicit in the digest."""
    (project / ".ucw" / "state" / "phase").write_text("scope")
    _run("pre-compact.py", {"cwd": str(project), "trigger": "manual"})
    digest = project / ".ucw" / "state" / "pre-compact-digest.md"
    text = digest.read_text()
    assert "Plan persisted: no" in text
    assert "Spec persisted: no" in text


# ---- subagent-stop -----------------------------------------------------------

def test_subagent_stop_logs(project):
    _run("subagent-stop.py", {
        "cwd": str(project), "agent_type": "planner", "agent_id": "p-1"
    })
    log = project / ".ucw" / "subagents.log"
    assert log.exists()
    assert "planner" in log.read_text()


# ---- post-tool-use edge cases -----------------------------------------------

def test_post_tool_use_json_check(project):
    target = project / "config.json"
    target.write_text('{"key": "val", broken json')
    _rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": str(target)}
    })
    body = json.loads(out)
    assert "failed" in body["hookSpecificOutput"]["additionalContext"]


def test_post_tool_use_unknown_extension_silent(project):
    target = project / "x.txt"
    target.write_text("hello")
    _rc, _out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": str(target)}
    })
    # No checker, no diagnostic — but streak still increments
    streak = (project / ".ucw" / "state" / "edit-streak").read_text().strip()
    assert streak == "1"


def test_post_tool_use_no_file_path_silent(project):
    _rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {}
    })
    assert out == ""


def test_post_tool_use_handles_nonexistent_file(project):
    rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": "/does/not/exist.py"}
    })
    assert rc == 0
    assert out == ""


def test_post_tool_use_rejects_path_outside_project(project):
    """Defense in depth: edits to /etc/passwd or other out-of-project paths
    must not increment the streak or trigger any check."""
    _rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": "/etc/passwd"}
    })
    assert out == ""
    streak_file = project / ".ucw" / "state" / "edit-streak"
    assert not streak_file.exists() or streak_file.read_text().strip() == "0"


def test_post_tool_use_rejects_relative_escape(project):
    """`../../../etc/passwd` resolves outside the project — must be ignored."""
    _rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": "../../../etc/passwd"}
    })
    assert out == ""
    streak_file = project / ".ucw" / "state" / "edit-streak"
    assert not streak_file.exists() or streak_file.read_text().strip() == "0"
