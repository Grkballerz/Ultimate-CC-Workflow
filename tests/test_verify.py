"""Tests for bin/ucw-verify.py — the project-aware verification runner."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_verify", REPO_ROOT / "bin" / "ucw-verify.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_verify"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---- detection ------------------------------------------------------------

def test_select_command_returns_none_when_no_signals(tmp_path):
    mod = _load()
    assert mod.select_command(tmp_path) is None


def test_select_command_picks_makefile_test_target(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")
    cmd, source = mod.select_command(tmp_path)
    assert cmd == ["make", "test"]
    assert source == "makefile:test"


def test_select_command_ignores_makefile_without_test_target(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text("build:\n\t@true\n")
    # falls through to stack detect — which finds nothing → returns None
    assert mod.select_command(tmp_path) is None


def test_select_command_preferences_overrides_makefile(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")
    knowledge = tmp_path / ".ucw" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "PREFERENCES.md").write_text(
        "| test_runner | pytest |\n"
    )
    cmd, source = mod.select_command(tmp_path)
    assert cmd == ["pytest", "-q", "--no-header"]
    assert source == "preferences:pytest"


# ---- run_verification -----------------------------------------------------

def test_run_verification_passes_on_true(tmp_path):
    mod = _load()
    result = mod.run_verification(tmp_path, timeout=10, command=["true"])
    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert result["timed_out"] is False


def test_run_verification_fails_on_false(tmp_path):
    mod = _load()
    result = mod.run_verification(tmp_path, timeout=10, command=["false"])
    assert result["passed"] is False
    assert result["exit_code"] == 1


def test_run_verification_captures_output_tail(tmp_path):
    mod = _load()
    result = mod.run_verification(
        tmp_path, timeout=10,
        command=["sh", "-c", "echo 'specific failure line' >&2; exit 1"],
    )
    assert result["passed"] is False
    assert "specific failure line" in result["summary"]


def test_run_verification_times_out(tmp_path):
    mod = _load()
    result = mod.run_verification(
        tmp_path, timeout=1, command=["sleep", "5"]
    )
    assert result["passed"] is False
    assert result["timed_out"] is True
    assert "timed out" in result["summary"].lower()


def test_run_verification_handles_missing_binary(tmp_path):
    mod = _load()
    result = mod.run_verification(
        tmp_path, timeout=10, command=["nonexistent-verifier-binary"]
    )
    assert result["passed"] is False
    assert "not found" in result["summary"].lower()


def test_run_verification_skipped_when_no_runner(tmp_path):
    mod = _load()
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["passed"] is True
    assert result["skipped"] is True
    assert result["source"] == "skipped"


# ---- CLI ------------------------------------------------------------------

def test_cli_exit_zero_on_pass(tmp_path, capsys):
    mod = _load()
    rc = mod.main(["--repo", str(tmp_path), "--command", "true", "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["passed"] is True


def test_cli_exit_one_on_fail(tmp_path, capsys):
    mod = _load()
    rc = mod.main(["--repo", str(tmp_path), "--command", "false", "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body["passed"] is False


def test_cli_exit_two_on_timeout(tmp_path, capsys):
    mod = _load()
    rc = mod.main(["--repo", str(tmp_path), "--command", "sleep 5", "--timeout", "1"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert body["timed_out"] is True


def test_cli_exit_three_when_no_runner(tmp_path, capsys):
    mod = _load()
    rc = mod.main(["--repo", str(tmp_path), "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert body.get("skipped") is True


# ---- env / config ---------------------------------------------------------

def test_timeout_env_var_picked_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UCW_VERIFY_TIMEOUT", "1")
    mod = _load()
    rc = mod.main(["--repo", str(tmp_path), "--command", "sleep 5"])
    body = json.loads(capsys.readouterr().out)
    assert body["timed_out"] is True
    assert rc == 2


def test_invalid_timeout_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UCW_VERIFY_TIMEOUT", "notanint")
    mod = _load()
    assert mod._default_timeout() == 60
