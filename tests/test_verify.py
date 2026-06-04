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


# ---- multi-gate suite -----------------------------------------------------
# The bug we fix here: verify used to run JUST the test runner. A project
# with passing vitest but broken `tsc` / eslint would slip through and the
# Stop hook would happily advance phase to verify and (at level 3) auto-ship
# broken code. Now lint + types + tests are all gates and any failure blocks.

def test_select_gates_returns_makefile_targets_for_all_three(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@true\n"
        "typecheck:\n\t@true\n"
        "test:\n\t@true\n"
    )
    plan = mod.select_gates(tmp_path)
    names = [g["name"] for g in plan]
    assert names == ["lint", "types", "tests"]
    assert plan[0]["command"] == ["make", "lint"]
    assert plan[1]["command"] == ["make", "typecheck"]
    assert plan[2]["command"] == ["make", "test"]


def test_select_gates_only_picks_what_exists(tmp_path):
    """A project with only a `test:` Makefile target → only tests gate runs."""
    mod = _load()
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")
    plan = mod.select_gates(tmp_path)
    assert [g["name"] for g in plan] == ["tests"]


def test_select_gates_package_json_scripts_with_pnpm(tmp_path):
    mod = _load()
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"lint": "eslint .", "typecheck": "tsc --noEmit", "test": "vitest run"}
    }))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    plan = mod.select_gates(tmp_path)
    assert [g["name"] for g in plan] == ["lint", "types", "tests"]
    for g in plan:
        assert g["command"][0] == "pnpm"
        assert g["command"][1] == "run"


def test_select_gates_preferences_overrides_makefile(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text("lint:\n\t@true\n")
    knowledge = tmp_path / ".ucw" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "PREFERENCES.md").write_text("| linter | ruff |\n")
    plan = mod.select_gates(tmp_path)
    lint = next(g for g in plan if g["name"] == "lint")
    assert lint["command"] == ["ruff", "check", "."]
    assert lint["source"] == "preferences:ruff"


def test_select_gates_ignores_template_placeholders(tmp_path):
    """Unfilled `{{linter}}` from the onboarder template must not be treated
    as a literal runner name."""
    mod = _load()
    knowledge = tmp_path / ".ucw" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "PREFERENCES.md").write_text("| linter | {{linter}} |\n")
    plan = mod.select_gates(tmp_path)
    assert not any(g["name"] == "lint" for g in plan)


def test_select_gates_only_filter(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@true\ntypecheck:\n\t@true\ntest:\n\t@true\n"
    )
    plan = mod.select_gates(tmp_path, only=["tests"])
    assert [g["name"] for g in plan] == ["tests"]


def test_run_verification_runs_all_gates_when_passing(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@true\ntypecheck:\n\t@true\ntest:\n\t@true\n"
    )
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["passed"] is True
    assert result["failed_gate"] is None
    assert [g["name"] for g in result["gates"]] == ["lint", "types", "tests"]
    assert all(g["passed"] for g in result["gates"])


def test_run_verification_stops_at_first_failing_gate(tmp_path):
    """Default behavior: if lint fails, don't run types or tests."""
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@echo 'lint busted' >&2 && false\n"
        "typecheck:\n\t@true\n"
        "test:\n\t@true\n"
    )
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["passed"] is False
    assert result["failed_gate"] == "lint"
    assert [g["name"] for g in result["gates"]] == ["lint"]
    assert "lint busted" in result["summary"]


def test_run_verification_top_level_command_points_at_failed_gate(tmp_path):
    """Back-compat: callers reading result['command'] should see the FAILED
    gate's command, not the last (passing) one."""
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@true\n"
        "typecheck:\n\t@false\n"
        "test:\n\t@true\n"
    )
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["failed_gate"] == "types"
    assert result["command"] == "make typecheck"
    assert result["passed"] is False


def test_run_verification_all_flag_runs_every_gate_despite_failures(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@false\ntypecheck:\n\t@false\ntest:\n\t@true\n"
    )
    result = mod.run_verification(tmp_path, timeout=10, run_all=True)
    assert result["passed"] is False
    assert [g["name"] for g in result["gates"]] == ["lint", "types", "tests"]
    # The top-level points at the FIRST failing gate so the user sees the
    # root-cause hit first.
    assert result["failed_gate"] == "lint"


def test_run_verification_only_filter_runs_subset(tmp_path):
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@false\ntypecheck:\n\t@true\ntest:\n\t@true\n"
    )
    result = mod.run_verification(tmp_path, timeout=10, only=["tests"])
    assert result["passed"] is True
    assert [g["name"] for g in result["gates"]] == ["tests"]


def test_cli_exit_one_when_lint_fails_even_though_tests_pass(tmp_path, capsys):
    """The whole point of the change: vitest-style 'only tests counted' is fixed."""
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@false\ntest:\n\t@true\n"
    )
    rc = mod.main(["--repo", str(tmp_path), "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body["passed"] is False
    assert body["failed_gate"] == "lint"


def test_cli_gates_flag_limits_to_subset(tmp_path, capsys):
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@false\ntest:\n\t@true\n"
    )
    rc = mod.main([
        "--repo", str(tmp_path), "--timeout", "10", "--gates", "tests",
    ])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [g["name"] for g in body["gates"]] == ["tests"]


def test_env_gates_filter_picked_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UCW_VERIFY_GATES", "tests")
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@false\ntest:\n\t@true\n"
    )
    rc = mod.main(["--repo", str(tmp_path), "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [g["name"] for g in body["gates"]] == ["tests"]


def test_select_command_back_compat_returns_tests(tmp_path):
    """Older callers still call select_command(); must return the tests pick."""
    mod = _load()
    (tmp_path / "Makefile").write_text(
        "lint:\n\t@true\ntest:\n\t@true\n"
    )
    cmd, source = mod.select_command(tmp_path)
    assert cmd == ["make", "test"]
    assert source == "makefile:test"
