"""Lock in compliance with Claude Code's strict hook output schema.

Per the harness error the user hit in PR #5 testing:

  Expected schema:
  {
    "continue": bool?, "suppressOutput": bool?, "stopReason": str?,
    "decision": "approve" | "block" | None,
    "reason": str?, "systemMessage": str?,
    "permissionDecision": "allow" | "deny" | "ask" | None,
    "hookSpecificOutput": { ...one of: PreToolUse, UserPromptSubmit,
                            PostToolUse, PostToolBatch }
  }

`hookSpecificOutput` is the offender — it's only valid for four events.
Emitting it on any other event triggers `Invalid input` validation, even
when the top-level `decision` is honored.

This module asserts each hook's output (under typical block / inject
conditions) matches the schema. Net new hooks should add an entry.
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

# Events whose hookSpecificOutput field IS valid.
HOOK_OUTPUT_OK_FOR = {"PreToolUse", "UserPromptSubmit",
                      "PostToolUse", "PostToolBatch"}

# Top-level keys the schema allows.
TOP_LEVEL_ALLOWED = {
    "continue", "suppressOutput", "stopReason", "decision", "reason",
    "systemMessage", "permissionDecision", "hookSpecificOutput",
}


def _run(hook: str, payload: dict) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/memory"
    cp = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=10,
    )
    return cp.returncode, cp.stdout, cp.stderr


def _validate(out: str, *, event_name: str) -> None:
    """Raise AssertionError if `out` doesn't match the strict schema for `event_name`."""
    out = out.strip()
    if not out:
        return  # silent (no output) is always valid
    body = json.loads(out)
    assert isinstance(body, dict), f"hook output must be a JSON object, got {type(body)}"

    unknown = set(body.keys()) - TOP_LEVEL_ALLOWED
    assert not unknown, f"unknown top-level keys: {unknown}"

    if "hookSpecificOutput" in body:
        assert event_name in HOOK_OUTPUT_OK_FOR, (
            f"hookSpecificOutput is not allowed for {event_name!r} per the "
            f"strict schema. Allowed events: {sorted(HOOK_OUTPUT_OK_FOR)}"
        )
        inner = body["hookSpecificOutput"]
        assert isinstance(inner, dict)
        assert inner.get("hookEventName") == event_name, (
            f"hookSpecificOutput.hookEventName must equal {event_name!r}"
        )

    if "decision" in body:
        assert body["decision"] in {"approve", "block"}, \
            f"decision must be 'approve' or 'block', got {body['decision']!r}"

    if "permissionDecision" in body:
        assert body["permissionDecision"] in {"allow", "deny", "ask"}, \
            f"permissionDecision invalid: {body['permissionDecision']!r}"


# ---- per-event compliance tests -------------------------------------------

@pytest.fixture
def project(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    return tmp_path


def test_stop_block_response_is_schema_compliant(project):
    # Force a deterministic auto-verify failure so we get a block response.
    (project / "Makefile").write_text("test:\n\t@false\n")
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _, out, _ = _run("stop.py", {"cwd": str(project)})
    _validate(out, event_name="Stop")


def test_session_start_emits_nothing(project):
    """SessionStart is now side-effect only."""
    _, out, _ = _run("session-start.py", {"cwd": str(project)})
    assert out.strip() == "", \
        "SessionStart should emit no output (strict schema rejects hookSpecificOutput)"


def test_subagent_stop_emits_nothing(project):
    _, out, _ = _run("subagent-stop.py", {
        "cwd": str(project), "agent_type": "planner", "agent_id": "p-1"
    })
    _validate(out, event_name="SubagentStop")


def test_pre_compact_emits_nothing(project):
    _, out, _ = _run("pre-compact.py", {"cwd": str(project), "trigger": "manual"})
    _validate(out, event_name="PreCompact")


def test_session_end_emits_nothing(project):
    _, out, _ = _run("session-end.py", {"cwd": str(project)})
    _validate(out, event_name="SessionEnd")


def test_pre_tool_use_deny_response_is_schema_compliant(project):
    _, out, _ = _run("pre-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"command": "rm -rf /"},
    })
    _validate(out, event_name="PreToolUse")


def test_post_tool_use_diagnostic_response_is_schema_compliant(project):
    target = project / "bad.py"
    target.write_text("def broken(:\n")
    _, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": str(target)}
    })
    _validate(out, event_name="PostToolUse")


def test_post_tool_batch_streak_breaker_is_schema_compliant(project):
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    _, out, _ = _run("post-tool-batch.py", {"cwd": str(project), "tools": []})
    _validate(out, event_name="PostToolBatch")


def test_user_prompt_submit_injection_is_schema_compliant(project):
    knowledge = project / ".ucw" / "knowledge"
    knowledge.mkdir()
    (knowledge / "STACK.md").write_text("# Stack\n- python 3.12\n")
    _, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "What stack are we using? check dependencies",
    })
    _validate(out, event_name="UserPromptSubmit")


# ---- UCW state edits don't count toward streak ----------------------------

def test_planner_writing_state_does_not_increment_streak(project):
    """The planner writes .ucw/state/plan.md as part of /ucw plan. That
    must NOT count as an unverified code edit — otherwise Stop blocks
    in build phase even when no real code changed (the bug the user hit).
    """
    plan = project / ".ucw" / "state" / "plan.md"
    plan.write_text("# Plan\n1. add /healthz\n")
    _, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": str(plan)}
    })
    assert out.strip() == "", "UCW state edits must not produce diagnostic output"
    streak = project / ".ucw" / "state" / "edit-streak"
    assert not streak.exists() or streak.read_text().strip() == "0", \
        "writing .ucw/state/plan.md must not bump the edit streak"


def test_review_findings_log_does_not_increment_streak(project):
    """Same rule for .ucw/reviews/<sha>/findings.jsonl."""
    rev_dir = project / ".ucw" / "reviews" / "abc"
    rev_dir.mkdir(parents=True)
    findings = rev_dir / "findings.jsonl"
    findings.write_text('{"id":"f-1","severity":"minor"}\n')
    _, out, _ = _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": str(findings)}
    })
    assert out.strip() == ""
    streak = project / ".ucw" / "state" / "edit-streak"
    assert not streak.exists() or streak.read_text().strip() == "0"


def test_real_code_edit_does_increment_streak(project):
    """Sanity check the negative case — actual project code still bumps streak."""
    real = project / "app.py"
    real.write_text("def hello(): pass\n")
    _run("post-tool-use.py", {
        "cwd": str(project), "tool_input": {"file_path": str(real)}
    })
    streak = project / ".ucw" / "state" / "edit-streak"
    assert streak.exists()
    assert streak.read_text().strip() == "1"
