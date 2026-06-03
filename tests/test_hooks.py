"""Tests for the hook scripts. Each hook is invoked as a subprocess with a
crafted JSON payload, and its stdout/exit code are asserted."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"


def _run(hook: str, payload: dict, cwd: Path | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True, text=True, cwd=cwd, env=env, timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    return tmp_path


# ---- session-start -----------------------------------------------------------
# SessionStart no longer emits hookSpecificOutput (Claude Code's strict
# schema rejects it for this event). The hook is side-effect only now.

def test_session_start_no_ucw_emits_no_output(tmp_path):
    rc, out, _ = _run("session-start.py", {"cwd": str(tmp_path)})
    assert rc == 0
    assert out == "", "session-start must not emit JSON (strict schema)"


def test_session_start_records_to_state(project):
    """SessionStart writes .ucw/state/last-session-start as a side effect."""
    rc, out, _ = _run("session-start.py", {
        "cwd": str(project), "session_id": "s123", "source": "startup",
    })
    assert rc == 0
    assert out == ""  # no JSON output
    marker = project / ".ucw" / "state" / "last-session-start"
    assert marker.exists()
    assert "s123" in marker.read_text()


# ---- pre-tool-use ------------------------------------------------------------

def test_pre_tool_use_blocks_rm_rf_root(project):
    rc, out, _ = _run("pre-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"command": "rm -rf /"},
    })
    assert rc == 0
    body = json.loads(out)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_use_allows_safe_bash(project):
    rc, out, _ = _run("pre-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"command": "ls -la"},
    })
    assert rc == 0
    assert out == ""  # no decision = pass through


def test_pre_tool_use_blocks_force_push_to_main(project):
    _rc, out, _ = _run("pre-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"command": "git push --force origin main"},
    })
    body = json.loads(out)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- post-tool-use -----------------------------------------------------------

def test_post_tool_use_increments_streak(project):
    target = project / "foo.py"
    target.write_text("x = 1\n")
    _run("post-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"file_path": str(target)},
    })
    streak = (project / ".ucw" / "state" / "edit-streak").read_text().strip()
    assert streak == "1"


def test_post_tool_use_catches_python_syntax_error(project):
    target = project / "bad.py"
    target.write_text("def broken(:\n")  # syntax error
    _rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"file_path": str(target)},
    })
    body = json.loads(out)
    assert "additionalContext" in body["hookSpecificOutput"]
    assert "failed" in body["hookSpecificOutput"]["additionalContext"]


# ---- post-tool-batch ---------------------------------------------------------

def test_post_tool_batch_streak_breaker(project):
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    _rc, out, _ = _run("post-tool-batch.py", {
        "cwd": str(project),
        "tools": [],
    })
    body = json.loads(out)
    assert body["decision"] == "block"


def test_post_tool_batch_resets_on_test_run(project):
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _run("post-tool-batch.py", {
        "cwd": str(project),
        "tools": [{"tool_input": {"command": "pytest tests/"}}],
    })
    assert (project / ".ucw" / "state" / "edit-streak").read_text().strip() == "0"


# ---- user-prompt-submit ------------------------------------------------------

def test_user_prompt_submit_injects_stack_doc(project):
    knowledge = project / ".ucw" / "knowledge"
    knowledge.mkdir()
    (knowledge / "STACK.md").write_text("# Stack\n- python 3.12\n- flask\n")
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "What version of flask are we on? Check the package install.",
    })
    body = json.loads(out)
    assert "Stack" in body["hookSpecificOutput"]["additionalContext"]
    assert "flask" in body["hookSpecificOutput"]["additionalContext"]


def test_user_prompt_submit_no_match_no_output(project):
    knowledge = project / ".ucw" / "knowledge"
    knowledge.mkdir()
    (knowledge / "STACK.md").write_text("# Stack\n")
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "Hello there.",
    })
    assert out == ""


def test_user_prompt_submit_injects_resume_hint_when_phase_set(project):
    """PR D: when workflow state is in flight, every prompt gets a small
    resume hint prepended so the agent re-orients after /clear."""
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "Hello there.",  # no keyword match — hint is the only injection
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "UCW resume" in ctx
    assert "phase=`build`" in ctx
    assert "/ucw resume" in ctx


def test_user_prompt_submit_no_resume_hint_without_phase(project):
    """No phase → no hint. Mostly defends against polluting clean projects."""
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "Hello there.",
    })
    assert out == ""


def test_user_prompt_submit_resume_hint_with_auto_mode(project):
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 3, "since": "2026-06-01T00:00:00Z"})
    )
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "do the next step",
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "auto-mode=L3" in ctx


def test_user_prompt_submit_resume_hint_with_retry_budget(project):
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 2})
    )
    (project / ".ucw" / "state" / "auto-retries").write_text("1")
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "do the next step",
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "retries 1/3" in ctx


def test_user_prompt_submit_resume_hint_then_knowledge(project):
    """When both fire, hint comes first so it's not buried beneath knowledge."""
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    knowledge = project / ".ucw" / "knowledge"
    knowledge.mkdir()
    (knowledge / "STACK.md").write_text("# Stack\n- python\n")
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "what version of python is on the stack?",
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert ctx.index("UCW resume") < ctx.index("STACK.md")


def test_user_prompt_submit_resume_hint_points_to_plan_and_spec_when_present(project):
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    (project / ".ucw" / "state" / "plan.md").write_text("# Plan\n")
    (project / ".ucw" / "state" / "spec.md").write_text("# Spec\n")
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "next step",
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert ".ucw/state/plan.md" in ctx
    assert ".ucw/state/spec.md" in ctx
