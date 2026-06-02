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

def test_stop_blocks_in_build_with_streak(project):
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "Verify" in body["reason"] or "verify" in body["reason"]


def test_stop_does_not_emit_hookSpecificOutput(project):
    """Regression: Claude Code's strict schema only allows hookSpecificOutput
    on PreToolUse / UserPromptSubmit / PostToolUse / PostToolBatch.
    Emitting it on Stop produces `Invalid input` validation errors.
    """
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    _rc, out, _ = _run("stop.py", {"cwd": str(project)})
    body = json.loads(out)
    assert "hookSpecificOutput" not in body, (
        "stop.py emitted hookSpecificOutput — Claude Code's strict hook "
        "schema rejects this field for Stop. Use decision + reason only."
    )


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
