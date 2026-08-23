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


@pytest.mark.parametrize("command", [
    "rm -rf /" + "*",                 # glob-on-root (built so this file is safe to grep)
    "rm -fr /",                       # reordered short flags
    "rm -rf $HOME",                   # env-var indirection to home
    'rm -rf "$HOME"/x',               # quoted $HOME
    "git push -f origin main",        # short force flag (was missed)
    "git push -fu origin master",     # combined short flags
    "git push origin main --force-with-lease",
])
def test_pre_tool_use_blocks_destructive_variants(project, command):
    _rc, out, _ = _run("pre-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"command": command},
    })
    body = json.loads(out)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", [
    "rm -rf node_modules",            # legitimate cleanup must not be blocked
    "rm -rf /tmp/build",
    "git push -f origin scratch",     # force-push to a non-main branch is allowed
])
def test_pre_tool_use_allows_legitimate_variants(project, command):
    rc, out, _ = _run("pre-tool-use.py", {
        "cwd": str(project),
        "tool_input": {"command": command},
    })
    assert rc == 0
    assert out == ""


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
    """At threshold with auto-verify disabled, the hook falls back to the
    classic 'agent must run something' block. (When auto-verify is on it
    silently runs `bin/ucw-verify.py --gates lint,types` and only blocks
    on actual gate failures — covered in test_post_tool_batch_detection.py.)
    """
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/memory"
    env["UCW_AUTO_STREAK_VERIFY"] = "0"
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "post-tool-batch.py")],
        input=json.dumps({"cwd": str(project), "tools": []}),
        capture_output=True, text=True, env=env, timeout=10,
    )
    body = json.loads(cp.stdout)
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


# ---- user-prompt-submit: pinned facts (QW4) ---------------------------------

def _seed_memory_db(project: Path, n_pins: int = 2, reason: str | None = None) -> list[int]:
    """Create .ucw/memory.sqlite with n pinned facts, oldest first."""
    import time as _time

    from ucw_memory.db import MemoryDB
    now = int(_time.time())
    ids = []
    with MemoryDB(project / ".ucw" / "memory.sqlite") as db:
        for i in range(n_pins):
            fid = db.note(
                scope="project", subject=f"rule-{i}", predicate="is",
                object_=f"pinned-object-{i}",
                reason=reason or f"pinned reason {i}",
            )
            db.conn.execute(
                "UPDATE facts SET created_at = ? WHERE id = ?", (now - 1000 + i, fid)
            )
            db.pin(fid)
            ids.append(fid)
        db.conn.commit()
    return ids


def test_user_prompt_submit_injects_pinned_facts_on_first_prompt(project):
    _seed_memory_db(project, n_pins=2)
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project), "session_id": "sess-1",
        "prompt": "Hello there.",  # no keyword match — pinned block is the only injection
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "UCW Memory (pinned)" in ctx
    assert "pinned-object-0" in ctx
    assert "pinned-object-1" in ctx
    # Most recent pin first
    assert ctx.index("pinned-object-1") < ctx.index("pinned-object-0")
    # Marker recorded for session gating
    assert (project / ".ucw" / "state" / "pinned-injected").read_text().strip() == "sess-1"


def test_user_prompt_submit_pinned_facts_only_once_per_session(project):
    _seed_memory_db(project, n_pins=1)
    payload = {"cwd": str(project), "session_id": "sess-1", "prompt": "Hello there."}
    _rc, out1, _ = _run("user-prompt-submit.py", payload)
    assert "UCW Memory (pinned)" in out1
    # Same session, second prompt → nothing injected
    _rc, out2, _ = _run("user-prompt-submit.py", payload)
    assert out2 == ""
    # New session → injected again
    _rc, out3, _ = _run("user-prompt-submit.py", {**payload, "session_id": "sess-2"})
    assert "UCW Memory (pinned)" in out3


def test_user_prompt_submit_pinned_facts_respects_char_budget(project):
    _seed_memory_db(project, n_pins=20, reason="x" * 80)
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project), "session_id": "sess-1", "prompt": "Hello there.",
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "UCW Memory (pinned)" in ctx
    assert len(ctx) <= 600
    # 20 pins at ~100 chars each can't all fit in 600
    assert ctx.count("pinned-object-") < 20


def test_user_prompt_submit_no_pinned_block_without_memory_db(project):
    """No memory DB → no pinned block (and no marker side effects)."""
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project), "session_id": "sess-1", "prompt": "Hello there.",
    })
    assert out == ""
    assert not (project / ".ucw" / "state" / "pinned-injected").exists()


def test_user_prompt_submit_pinned_facts_fallback_session_marker(project):
    """Without a payload session_id, the hook reuses last-session-start
    (written by session-start.py) to distinguish sessions."""
    _seed_memory_db(project, n_pins=1)
    lss = project / ".ucw" / "state" / "last-session-start"
    lss.write_text("1000\ts-alpha\tstartup\n")
    payload = {"cwd": str(project), "prompt": "Hello there."}
    _rc, out1, _ = _run("user-prompt-submit.py", payload)
    assert "UCW Memory (pinned)" in out1
    _rc, out2, _ = _run("user-prompt-submit.py", payload)
    assert out2 == ""
    # New session start recorded → inject again
    lss.write_text("2000\ts-beta\tstartup\n")
    _rc, out3, _ = _run("user-prompt-submit.py", payload)
    assert "UCW Memory (pinned)" in out3


def test_user_prompt_submit_pinned_block_after_resume_hint(project):
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    _seed_memory_db(project, n_pins=1)
    _rc, out, _ = _run("user-prompt-submit.py", {
        "cwd": str(project), "session_id": "sess-1", "prompt": "Hello there.",
    })
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert ctx.index("UCW resume") < ctx.index("UCW Memory (pinned)")
