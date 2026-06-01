"""Tests for dashboard/statusline.sh — fast at-a-glance status for Claude Code."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUSLINE = REPO_ROOT / "dashboard" / "statusline.sh"


def _run(env: dict | None = None) -> tuple[int, str, str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    cp = subprocess.run(["bash", str(STATUSLINE)], capture_output=True, text=True, env=e, timeout=5)
    return cp.returncode, cp.stdout, cp.stderr


def test_no_ucw_dir_says_no_ucw(tmp_path):
    rc, out, _ = _run({"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert rc == 0
    assert "no .ucw" in out


def test_reads_phase_and_streak(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("build")
    (tmp_path / ".ucw" / "state" / "edit-streak").write_text("3")
    rc, out, _ = _run({"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert rc == 0
    assert "phase: build" in out
    assert "3" in out


def test_streak_highlighted_when_high(tmp_path):
    """Red ANSI code should appear when streak ≥ 5."""
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("build")
    (tmp_path / ".ucw" / "state" / "edit-streak").write_text("6")
    rc, out, _ = _run({"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert rc == 0
    # ANSI red opening sequence
    assert "\033[31m" in out or "[31m" in out


def test_no_phase_shows_dash(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    rc, out, _ = _run({"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert rc == 0
    assert "phase: —" in out


def test_walks_up_for_ucw_dir(tmp_path):
    """statusline should walk up from CLAUDE_PROJECT_DIR to find .ucw/."""
    root = tmp_path / "project"
    nested = root / "deep" / "nested" / "subdir"
    nested.mkdir(parents=True)
    (root / ".ucw" / "state").mkdir(parents=True)
    (root / ".ucw" / "state" / "phase").write_text("plan")
    rc, out, _ = _run({"CLAUDE_PROJECT_DIR": str(nested)})
    assert rc == 0
    assert "phase: plan" in out


def test_runs_fast(tmp_path):
    """The statusline must be fast (<500ms wall clock) — measured loosely."""
    import time
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    start = time.perf_counter()
    _run({"CLAUDE_PROJECT_DIR": str(tmp_path)})
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"statusline took {elapsed:.2f}s (budget: <1s)"
