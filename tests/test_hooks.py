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

def test_session_start_suggests_init_when_no_ucw(tmp_path):
    rc, out, _ = _run("session-start.py", {"cwd": str(tmp_path)})
    assert rc == 0
    body = json.loads(out)
    assert "Run `/ucw init`" in body["hookSpecificOutput"]["additionalContext"]


def test_session_start_loads_index(project):
    knowledge = project / ".ucw" / "knowledge"
    knowledge.mkdir()
    (knowledge / "INDEX.md").write_text("# Knowledge Index\n- STACK.md — python/flask\n")
    rc, out, _ = _run("session-start.py", {"cwd": str(project)})
    assert rc == 0
    body = json.loads(out)
    assert "python/flask" in body["hookSpecificOutput"]["additionalContext"]


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
    rc, out, _ = _run("pre-tool-use.py", {
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
    rc, out, _ = _run("post-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"file_path": str(target)},
    })
    body = json.loads(out)
    assert "additionalContext" in body["hookSpecificOutput"]
    assert "failed" in body["hookSpecificOutput"]["additionalContext"]


# ---- post-tool-batch ---------------------------------------------------------

def test_post_tool_batch_streak_breaker(project):
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    rc, out, _ = _run("post-tool-batch.py", {
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
    rc, out, _ = _run("user-prompt-submit.py", {
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
    rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project),
        "prompt": "Hello there.",
    })
    assert out == ""
