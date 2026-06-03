"""Tests for Stop hook auto-mode behavior at levels 2 and 3 (PR B).

Level 1 was a planner-side change (no hook impact). Levels 2-3 live in
hooks/stop.py:

  - Level 2: on verify failure, bump `.ucw/state/auto-retries` and block
    with a retry-N/cap message until cap is hit (then hard block).
  - Level 3: on verify pass, instead of allowing Stop, block with a
    "running /ucw ship now" instruction so the agent immediately runs ship.

These tests use a project-local `Makefile` (passing or failing) so
`bin/ucw-verify.py` picks `make test` as the runner — same pattern as
`test_hooks_more.py::test_stop_*`.
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


def _run(payload: dict, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/memory"
    env.pop("UCW_AUTO_MODE", None)
    env.pop("UCW_AUTO_RETRY_CAP", None)
    env.pop("UCW_SKIP_AUTO_VERIFY", None)
    if env_extra:
        env.update(env_extra)
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "stop.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=20,
    )
    return cp.returncode, cp.stdout, cp.stderr


def _build_setup(tmp_path: Path, *, passing: bool) -> Path:
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("build")
    (tmp_path / ".ucw" / "state" / "edit-streak").write_text("3")
    cmd = "@true" if passing else "@false"
    (tmp_path / "Makefile").write_text(f"test:\n\t{cmd}\n")
    return tmp_path


def _set_level(project: Path, level: int, retry_cap: int | None = None) -> None:
    payload = {"level": level, "since": "2026-01-01T00:00:00Z"}
    if retry_cap is not None:
        payload["retry_cap"] = retry_cap
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps(payload))


@pytest.fixture
def failing_build(tmp_path):
    return _build_setup(tmp_path, passing=False)


@pytest.fixture
def passing_build(tmp_path):
    return _build_setup(tmp_path, passing=True)


# ---- Level 2: retry loop ----------------------------------------------------

def test_level_2_first_failure_increments_retry_to_1(failing_build):
    _set_level(failing_build, 2)
    _rc, out, _ = _run({"cwd": str(failing_build)})
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "retry 1/3" in body["reason"]
    assert (failing_build / ".ucw" / "state" / "auto-retries").read_text().strip() == "1"


def test_level_2_retry_counter_persists_across_calls(failing_build):
    _set_level(failing_build, 2)
    _run({"cwd": str(failing_build)})
    _run({"cwd": str(failing_build)})
    _rc, out, _ = _run({"cwd": str(failing_build)})
    body = json.loads(out)
    # Third call should be retry 3/3 → cap exhausted message
    assert "retry cap" in body["reason"].lower() or "exhausted" in body["reason"].lower()


def test_level_2_cap_exhausted_clears_counter(failing_build):
    """After hitting the cap, counter resets so a successful manual fix
    + re-enable starts fresh."""
    _set_level(failing_build, 2, retry_cap=2)
    _run({"cwd": str(failing_build)})  # 1
    _run({"cwd": str(failing_build)})  # 2 — at cap
    assert not (failing_build / ".ucw" / "state" / "auto-retries").exists(), \
        "counter must clear when cap is hit"


def test_level_2_custom_retry_cap_via_state(failing_build):
    _set_level(failing_build, 2, retry_cap=1)
    _rc, out, _ = _run({"cwd": str(failing_build)})
    body = json.loads(out)
    # cap=1 means even the first failure is at cap
    assert "exhausted" in body["reason"].lower() or "cap" in body["reason"].lower()


def test_level_2_custom_retry_cap_via_env(failing_build):
    _set_level(failing_build, 2)
    _rc, out, _ = _run({"cwd": str(failing_build)},
                       env_extra={"UCW_AUTO_RETRY_CAP": "1"})
    body = json.loads(out)
    assert "exhausted" in body["reason"].lower() or "cap" in body["reason"].lower()


def test_level_2_verify_pass_resets_retry_counter(passing_build):
    _set_level(passing_build, 2)
    # Simulate a prior failed retry
    (passing_build / ".ucw" / "state" / "auto-retries").write_text("2")
    _run({"cwd": str(passing_build)})
    assert not (passing_build / ".ucw" / "state" / "auto-retries").exists(), \
        "retry counter must clear on verify pass"


def test_level_2_off_falls_back_to_hard_block_on_fail(failing_build):
    # level=0: classic hard-block behavior, no retry counter
    _rc, out, _ = _run({"cwd": str(failing_build)})  # no auto-mode state
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "retry" not in body["reason"].lower()
    assert not (failing_build / ".ucw" / "state" / "auto-retries").exists()


def test_level_2_pass_response_does_not_emit_ship_message(passing_build):
    """At level 2 (but < 3), a pass should still silently allow Stop."""
    _set_level(passing_build, 2)
    _rc, out, _ = _run({"cwd": str(passing_build)})
    assert out == "", "level 2 pass should not emit ship-nudge (that's level 3)"


# ---- Level 3: auto-ship nudge -----------------------------------------------

def test_level_3_verify_pass_blocks_with_run_ship_message(passing_build):
    _set_level(passing_build, 3)
    _rc, out, _ = _run({"cwd": str(passing_build)})
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "/ucw ship" in body["reason"]
    assert "AUTO" in body["reason"]


def test_level_3_verify_pass_still_clears_streak_and_advances_phase(passing_build):
    _set_level(passing_build, 3)
    _run({"cwd": str(passing_build)})
    streak = passing_build / ".ucw" / "state" / "edit-streak"
    assert not streak.exists() or streak.read_text().strip() == "0"
    assert (passing_build / ".ucw" / "state" / "phase").read_text().strip() == "verify"


def test_level_4_verify_pass_also_blocks_with_ship_nudge(passing_build):
    """Level 4 inherits level 3 behavior — PR C will layer PR creation on top."""
    _set_level(passing_build, 4)
    _rc, out, _ = _run({"cwd": str(passing_build)})
    body = json.loads(out)
    assert "/ucw ship" in body["reason"]


def test_level_3_verify_fail_uses_level_2_retry_loop(failing_build):
    """Level 3 is cumulative — failures still trigger the retry loop."""
    _set_level(failing_build, 3)
    _rc, out, _ = _run({"cwd": str(failing_build)})
    body = json.loads(out)
    assert "retry 1" in body["reason"]


# ---- env-var escape hatch --------------------------------------------------

def test_env_var_off_short_circuits_state_at_level_3(passing_build):
    _set_level(passing_build, 3)
    _rc, out, _ = _run({"cwd": str(passing_build)},
                       env_extra={"UCW_AUTO_MODE": "off"})
    assert out == "", "UCW_AUTO_MODE=off must short-circuit even at level 3"


def test_env_var_off_short_circuits_state_at_level_2(failing_build):
    _set_level(failing_build, 2)
    _rc, out, _ = _run({"cwd": str(failing_build)},
                       env_extra={"UCW_AUTO_MODE": "off"})
    body = json.loads(out)
    # Should fall through to legacy hard-block (no retry counter)
    assert body["decision"] == "block"
    assert "retry" not in body["reason"].lower()


# ---- schema compliance still holds ------------------------------------------

def test_level_2_block_has_no_hookSpecificOutput(failing_build):
    """Per PR #6, Stop must NEVER emit hookSpecificOutput."""
    _set_level(failing_build, 2)
    _rc, out, _ = _run({"cwd": str(failing_build)})
    body = json.loads(out)
    assert "hookSpecificOutput" not in body


def test_level_3_block_has_no_hookSpecificOutput(passing_build):
    _set_level(passing_build, 3)
    _rc, out, _ = _run({"cwd": str(passing_build)})
    body = json.loads(out)
    assert "hookSpecificOutput" not in body
    assert body["decision"] in {"block", "approve"}
