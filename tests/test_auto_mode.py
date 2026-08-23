"""Tests for bin/ucw-auto.py — the auto-mode state machine.

PR A of the auto-mode rollout: state + CLI + the `current_level()` helper
that hooks call. Levels 2-4 land in PR B/C — those tests live separately.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin" / "ucw-auto.py"

# Import the module directly for in-process tests of the helper functions
sys.path.insert(0, str(REPO_ROOT / "bin"))
import importlib.util

_spec = importlib.util.spec_from_file_location("ucw_auto", BIN)
ucw_auto = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ucw_auto)


def _run(args: list[str], cwd: Path, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    # Clear UCW_AUTO_MODE for deterministic CLI tests unless caller sets it
    if env_extra is None or "UCW_AUTO_MODE" not in env_extra:
        env.pop("UCW_AUTO_MODE", None)
    cp = subprocess.run(
        [sys.executable, str(BIN), *args],
        cwd=cwd, capture_output=True, text=True, env=env, timeout=10,
    )
    return cp.returncode, cp.stdout, cp.stderr


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    return tmp_path


# ---- on/off/status round-trip ----------------------------------------------

def test_on_default_level_is_4(project):
    rc, out, _ = _run(["on"], cwd=project)
    assert rc == 0
    body = json.loads(out)
    assert body["auto_mode"] == "on"
    assert body["level"] == 4
    assert "since" in body
    state = project / ".ucw" / "state" / "auto-mode"
    assert state.exists()


def test_on_explicit_level(project):
    rc, out, _ = _run(["on", "2"], cwd=project)
    assert rc == 0
    assert json.loads(out)["level"] == 2


def test_on_invalid_level_rejected(project):
    rc, _out, err = _run(["on", "9"], cwd=project)
    assert rc == 2
    assert "invalid level" in err


def test_on_then_off_clears_state(project):
    _run(["on", "3"], cwd=project)
    _rc, out, _ = _run(["off"], cwd=project)
    body = json.loads(out)
    assert body["auto_mode"] == "off"
    assert body["cleared"] is True
    assert not (project / ".ucw" / "state" / "auto-mode").exists()


def test_off_when_already_off_is_idempotent(project):
    rc, out, _ = _run(["off"], cwd=project)
    assert rc == 0
    body = json.loads(out)
    assert body["auto_mode"] == "off"
    assert body["cleared"] is False


def test_status_when_off(project):
    _rc, out, _ = _run(["status"], cwd=project)
    body = json.loads(out)
    assert body["auto_mode"] == "off"
    assert body["level"] == 0


def test_status_when_on(project):
    _run(["on", "2"], cwd=project)
    _rc, out, _ = _run(["status"], cwd=project)
    body = json.loads(out)
    assert body["auto_mode"] == "on"
    assert body["level"] == 2
    assert body["retry_cap"] == 3
    assert body["source"] == "state"


def test_level_subcommand_prints_integer(project):
    _run(["on", "1"], cwd=project)
    rc, out, _ = _run(["level"], cwd=project)
    assert rc == 0
    assert out.strip() == "1"


def test_level_when_off_is_zero(project):
    _rc, out, _ = _run(["level"], cwd=project)
    assert out.strip() == "0"


# ---- retry-cap configuration -----------------------------------------------

def test_retry_cap_override(project):
    rc, out, _ = _run(["on", "2", "--retry-cap", "5"], cwd=project)
    assert rc == 0
    rc, out, _ = _run(["status"], cwd=project)
    assert json.loads(out)["retry_cap"] == 5


def test_retry_cap_env_var_wins(project):
    _run(["on", "2", "--retry-cap", "5"], cwd=project)
    _rc, out, _ = _run(["status"], cwd=project, env_extra={"UCW_AUTO_RETRY_CAP": "7"})
    body = json.loads(out)
    assert body["retry_cap"] == 7


def test_retry_cap_invalid_env_falls_back_to_state(project):
    _run(["on", "2", "--retry-cap", "5"], cwd=project)
    _rc, out, _ = _run(["status"], cwd=project, env_extra={"UCW_AUTO_RETRY_CAP": "abc"})
    body = json.loads(out)
    assert body["retry_cap"] == 5


# ---- env-var override ------------------------------------------------------

def test_env_var_off_wins_over_state(project):
    """UCW_AUTO_MODE=off must short-circuit even if state file says on.
    This is the panic-button: if the agent goes wrong, the user can disable
    in one shell without finding the right state file."""
    _run(["on", "4"], cwd=project)
    _rc, out, _ = _run(["status"], cwd=project, env_extra={"UCW_AUTO_MODE": "off"})
    body = json.loads(out)
    assert body["auto_mode"] == "off"
    assert body["level"] == 0


def test_env_var_on_sets_level_4(project):
    _rc, out, _ = _run(["status"], cwd=project, env_extra={"UCW_AUTO_MODE": "on"})
    body = json.loads(out)
    assert body["auto_mode"] == "on"
    assert body["level"] == 4
    assert body["source"] == "env"


def test_env_var_explicit_level(project):
    _rc, out, _ = _run(["status"], cwd=project, env_extra={"UCW_AUTO_MODE": "3"})
    assert json.loads(out)["level"] == 3


def test_env_var_invalid_level_falls_back_to_state(project):
    _run(["on", "2"], cwd=project)
    _rc, out, _ = _run(["status"], cwd=project, env_extra={"UCW_AUTO_MODE": "99"})
    # Invalid env value → fall through to state
    assert json.loads(out)["level"] == 2


# ---- in-process helper functions -------------------------------------------

def test_current_level_handles_missing_state_file(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    assert ucw_auto.current_level(project) == 0


def test_current_level_handles_malformed_state(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    (project / ".ucw" / "state" / "auto-mode").write_text("not json")
    assert ucw_auto.current_level(project) == 0


def test_current_level_handles_unknown_level(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 99})
    )
    assert ucw_auto.current_level(project) == 0


def test_current_level_reads_valid_state(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 2, "since": "2026-01-01T00:00:00Z"})
    )
    assert ucw_auto.current_level(project) == 2


def test_current_level_env_off_wins(project, monkeypatch):
    monkeypatch.setenv("UCW_AUTO_MODE", "off")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 4})
    )
    assert ucw_auto.current_level(project) == 0


def test_current_retry_cap_default(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_RETRY_CAP", raising=False)
    assert ucw_auto.current_retry_cap(project) == 3


def test_current_retry_cap_from_state(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_RETRY_CAP", raising=False)
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 2, "retry_cap": 7})
    )
    assert ucw_auto.current_retry_cap(project) == 7


def test_current_retry_cap_env_wins(project, monkeypatch):
    monkeypatch.setenv("UCW_AUTO_RETRY_CAP", "11")
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": 2, "retry_cap": 7})
    )
    assert ucw_auto.current_retry_cap(project) == 11


def test_current_retry_cap_rejects_unreasonable_values(project, monkeypatch):
    monkeypatch.setenv("UCW_AUTO_RETRY_CAP", "9999")  # too high → fall through
    assert ucw_auto.current_retry_cap(project) == 3


# ---- state file lives in the right place ----------------------------------

def test_state_persisted_to_dot_ucw_state(project):
    _run(["on"], cwd=project)
    state = project / ".ucw" / "state" / "auto-mode"
    assert state.exists()
    body = json.loads(state.read_text())
    assert body["level"] == 4
    assert "since" in body


# ---- drift guard: bin/ucw-auto.py and hooks/_hook_common.py must agree -----
# Both have their own copy of the state-reading logic (bin/ for the CLI,
# hooks/ for self-contained hook scripts). These tests ensure they agree on
# the same inputs, so a future change to one without the other gets caught.

def _hook_level(project: Path, env_extra: dict | None = None) -> int:
    """Import hooks/_hook_common from the repo and call auto_mode_level."""
    spec = importlib.util.spec_from_file_location(
        "hook_common", REPO_ROOT / "hooks" / "_hook_common.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    payload = {"cwd": str(project)}
    if env_extra:
        for k, v in env_extra.items():
            os.environ[k] = v
    try:
        return mod.auto_mode_level(payload)
    finally:
        if env_extra:
            for k in env_extra:
                os.environ.pop(k, None)


def test_hook_helper_matches_bin_on_off(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    assert _hook_level(project) == ucw_auto.current_level(project) == 0


def test_hook_helper_matches_bin_on_each_level(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    for level in (1, 2, 3, 4):
        (project / ".ucw" / "state" / "auto-mode").write_text(
            json.dumps({"level": level})
        )
        assert _hook_level(project) == ucw_auto.current_level(project) == level


def test_hook_helper_matches_bin_on_env_off(project, monkeypatch):
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 4}))
    monkeypatch.setenv("UCW_AUTO_MODE", "off")
    assert _hook_level(project) == ucw_auto.current_level(project) == 0


def test_hook_helper_matches_bin_on_env_level(project, monkeypatch):
    monkeypatch.setenv("UCW_AUTO_MODE", "2")
    assert _hook_level(project) == ucw_auto.current_level(project) == 2


def test_hook_helper_matches_bin_on_malformed_state(project, monkeypatch):
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    (project / ".ucw" / "state" / "auto-mode").write_text("not json")
    assert _hook_level(project) == ucw_auto.current_level(project) == 0
