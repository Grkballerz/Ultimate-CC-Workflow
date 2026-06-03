"""Tests for bin/ucw-resume.py — the post-/clear re-orientation block."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin" / "ucw-resume.py"


def _run(args: list[str], cwd: Path, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.pop("UCW_AUTO_MODE", None)
    env.pop("UCW_AUTO_RETRY_CAP", None)
    if env_extra:
        env.update(env_extra)
    cp = subprocess.run(
        [sys.executable, str(BIN), *args],
        cwd=cwd, capture_output=True, text=True, env=env, timeout=10,
    )
    return cp.returncode, cp.stdout, cp.stderr


def _git(args: list[str], cwd: Path) -> None:
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com",
    })
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=cwd, check=True, env=env, capture_output=True, text=True,
    )


@pytest.fixture
def empty_project(tmp_path):
    """No .ucw, no git."""
    return tmp_path


@pytest.fixture
def state_only_project(tmp_path):
    """`.ucw/state/` exists with some workflow state, no git."""
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("build\n")
    return tmp_path


@pytest.fixture
def git_project(tmp_path):
    """Real git repo with one commit + .ucw state."""
    _git(["init", "-b", "main"], cwd=tmp_path)
    (tmp_path / "README.md").write_text("# r\n")
    _git(["add", "README.md"], cwd=tmp_path)
    _git(["commit", "-m", "Initial commit subject"], cwd=tmp_path)
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("build\n")
    return tmp_path


# ---- clean-slate -----------------------------------------------------------

def test_resume_with_no_state_prints_clean_slate(empty_project):
    rc, out, _ = _run([], cwd=empty_project)
    assert rc == 0
    assert "UCW Resume" in out
    assert "No UCW state" in out or "no work has started" in out


def test_resume_exit_code_zero_when_no_state(empty_project):
    """Clean slate is not an error — exit 0."""
    rc, _out, _ = _run([], cwd=empty_project)
    assert rc == 0


# ---- phase + state visibility ----------------------------------------------

def test_resume_prints_phase_when_set(state_only_project):
    rc, out, _ = _run([], cwd=state_only_project)
    assert rc == 0
    assert "Phase" in out
    assert "build" in out


def test_resume_includes_edit_streak_when_nonzero(state_only_project):
    (state_only_project / ".ucw" / "state" / "edit-streak").write_text("4")
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Edit streak" in out
    assert "4" in out


def test_resume_omits_edit_streak_when_zero(state_only_project):
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Edit streak" not in out


# ---- auto-mode -------------------------------------------------------------

def test_resume_includes_auto_mode_level_when_on(state_only_project):
    (state_only_project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 3, "since": "2026-06-01T10:00:00Z"})
    )
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Auto-mode" in out
    assert "level 3" in out
    assert "since 2026-06-01T10:00:00Z" in out


def test_resume_shows_auto_mode_off_when_no_state(state_only_project):
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Auto-mode" in out
    assert "off" in out.lower()


def test_resume_includes_retry_budget_at_level_2_plus(state_only_project):
    (state_only_project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 2, "since": "2026-06-01T10:00:00Z"})
    )
    (state_only_project / ".ucw" / "state" / "auto-retries").write_text("1")
    rc, out, _ = _run([], cwd=state_only_project)
    assert "retries 1/3" in out


def test_resume_respects_env_auto_mode_override(state_only_project):
    (state_only_project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 4})
    )
    rc, out, _ = _run([], cwd=state_only_project,
                      env_extra={"UCW_AUTO_MODE": "off"})
    # State says L4 but env says off — env wins
    assert "level 4" not in out
    assert "off" in out.lower()


# ---- spec + plan -----------------------------------------------------------

def test_resume_includes_spec_when_present(state_only_project):
    (state_only_project / ".ucw" / "state" / "spec.md").write_text(
        "# Spec: add healthz\n\nLiveness probe for the Flask app.\n"
    )
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Spec" in out
    assert "Liveness probe" in out


def test_resume_truncates_spec_at_byte_limit(state_only_project):
    long = "x" * 5000
    (state_only_project / ".ucw" / "state" / "spec.md").write_text(long)
    rc, out, _ = _run(["--spec-bytes", "500"], cwd=state_only_project)
    assert "truncated at 500" in out


def test_resume_includes_plan_when_present(state_only_project):
    (state_only_project / ".ucw" / "state" / "plan.md").write_text(
        "1. [A] Add /healthz endpoint   src/health.py\n2. [A] Add test   tests/test_health.py\n"
    )
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Plan" in out
    assert "Add /healthz endpoint" in out


def test_resume_truncates_plan_at_byte_limit(state_only_project):
    long = "x" * 5000
    (state_only_project / ".ucw" / "state" / "plan.md").write_text(long)
    rc, out, _ = _run(["--plan-bytes", "500"], cwd=state_only_project)
    assert "truncated at 500" in out


def test_resume_notes_missing_spec_when_phase_is_set(state_only_project):
    """If phase is set but spec.md doesn't exist (legacy state from before
    PR D), surface the gap so the user knows to re-run /ucw plan."""
    rc, out, _ = _run([], cwd=state_only_project)
    assert "No `.ucw/state/spec.md`" in out or "spec was never persisted" in out


# ---- git integration -------------------------------------------------------

def test_resume_includes_last_commit_sha_and_subject(git_project):
    rc, out, _ = _run([], cwd=git_project)
    assert "HEAD" in out
    assert "Initial commit subject" in out


def test_resume_includes_branch_when_not_HEAD(git_project):
    rc, out, _ = _run([], cwd=git_project)
    assert "main" in out


def test_resume_handles_non_git_repo(state_only_project):
    """No git → no SHA/branch sections, but resume still works."""
    rc, out, _ = _run([], cwd=state_only_project)
    assert rc == 0
    assert "## Git" not in out
    assert "Phase" in out  # other sections still render


# ---- dirty-tree warning ----------------------------------------------------

def test_resume_dirty_tree_warning_when_auto_on_and_phase_land(git_project):
    (git_project / ".ucw" / "state" / "phase").write_text("land\n")
    (git_project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 3}))
    # Make tree dirty
    (git_project / "new.py").write_text("x = 1\n")
    rc, out, _ = _run([], cwd=git_project)
    assert "Dirty tree" in out
    assert "WARNING" in out
    assert "crashed mid-ship" in out


def test_resume_dirty_tree_no_warning_when_phase_is_build(git_project):
    (git_project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 3}))
    (git_project / "new.py").write_text("x = 1\n")
    rc, out, _ = _run([], cwd=git_project)
    # phase=build, dirty tree is EXPECTED — warning should NOT fire
    assert "WARNING" not in out
    assert "Dirty tree" in out  # but the count is still listed


def test_resume_dirty_tree_no_warning_when_auto_off(git_project):
    (git_project / ".ucw" / "state" / "phase").write_text("land\n")
    # No auto-mode state → no auto-mode
    (git_project / "new.py").write_text("x = 1\n")
    rc, out, _ = _run([], cwd=git_project)
    assert "WARNING" not in out


# ---- pre-compact digest passthrough ----------------------------------------

def test_resume_includes_pre_compact_digest_when_present(state_only_project):
    (state_only_project / ".ucw" / "state" / "pre-compact-digest.md").write_text(
        "# Pre-compact digest\n\n- Phase at compact: build\n- Trigger: manual\n"
    )
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Pre-compact digest" in out
    assert "Trigger: manual" in out


# ---- knowledge inventory ---------------------------------------------------

def test_resume_lists_knowledge_files(state_only_project):
    (state_only_project / ".ucw" / "knowledge").mkdir()
    (state_only_project / ".ucw" / "knowledge" / "STACK.md").write_text("x")
    (state_only_project / ".ucw" / "knowledge" / "DESIGN.md").write_text("x")
    rc, out, _ = _run([], cwd=state_only_project)
    assert "Knowledge" in out
    assert "STACK.md" in out
    assert "DESIGN.md" in out


# ---- resume finds project root from a subdir -------------------------------

def test_resume_finds_project_root_from_subdir(git_project):
    subdir = git_project / "src" / "nested"
    subdir.mkdir(parents=True)
    rc, out, _ = _run([], cwd=subdir)
    assert rc == 0
    assert "Phase" in out
    assert "build" in out
