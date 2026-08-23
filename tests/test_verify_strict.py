"""Tests for verify hardening: probe-first tool resolution, strict gates,
the verify report + tree-keyed cache, and the Stop hook's strict handling.

Covers (WP1 + WP4):
- bin/ucw-verify.py resolves tools via <repo>/.venv/bin, <repo>/venv/bin,
  <repo>/node_modules/.bin, $UCW_HOME/venv/bin, then PATH — not bare which().
- Strict gates: phase verify/land or auto-mode level >= 2 turn a setup-skip
  (missing tool) into a gate FAILURE; non-strict setup-skip is preserved.
- .ucw/state/verify-report.json is always written; .ucw/state/last-verify.json
  caches passing runs keyed by HEAD sha + sha256(`git diff HEAD`), with
  --no-cache override and dirty-tree/commit invalidation.
- hooks/stop.py blocks (instead of clearing the streak) on skipped/setup-
  skipped verify results when auto-mode level >= 2.
- Makefile lint skips are labeled and `validate` qualifies its green line;
  install.sh provisions pytest + ruff into $UCW_HOME/venv (shell — asserted
  grep-style, same as the Makefile, since the repo has no shell test harness).
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "hooks"

MISSING_TOOL_LINE = "sh: 1: eslint: not found"


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_verify", REPO_ROOT / "bin" / "ucw-verify.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_verify"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Neutralize host state that would leak into strict/probe detection."""
    for var in ("UCW_AUTO_MODE", "UCW_VERIFY_STRICT", "UCW_VERIFY_GATES",
                "UCW_SKIP_AUTO_VERIFY", "UCW_AUTO_RETRY_CAP"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("UCW_HOME", str(tmp_path / "ucw-home"))
    return tmp_path


def _make_exec(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# ---- probe-first tool resolution -------------------------------------------

def test_resolve_tool_probe_order(clean_env, tmp_path):
    """<repo>/.venv/bin wins over venv/bin over node_modules/.bin over
    $UCW_HOME/venv/bin; PATH is the last resort."""
    mod = _load()
    ucw_home = Path(os.environ["UCW_HOME"])
    spots = [
        tmp_path / ".venv" / "bin" / "mytool",
        tmp_path / "venv" / "bin" / "mytool",
        tmp_path / "node_modules" / ".bin" / "mytool",
        ucw_home / "venv" / "bin" / "mytool",
    ]
    for spot in spots:
        _make_exec(spot)
    for expected in spots:
        assert mod._resolve_tool(tmp_path, "mytool") == str(expected)
        expected.unlink()
    # All probe dirs exhausted → PATH (which has no 'mytool')
    assert mod._resolve_tool(tmp_path, "mytool") is None


def test_resolve_tool_falls_back_to_path(clean_env, tmp_path):
    mod = _load()
    resolved = mod._resolve_tool(tmp_path, "sh")
    assert resolved is not None
    assert Path(resolved).name == "sh"


def test_gate_env_prepends_existing_probe_dirs(clean_env, tmp_path):
    mod = _load()
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    env = mod._gate_env(tmp_path)
    first = env["PATH"].split(os.pathsep)[0]
    assert first == str(tmp_path / ".venv" / "bin")


def test_gate_runs_tool_from_project_venv(clean_env, tmp_path):
    """A linter that only exists in <repo>/.venv/bin must be found and run."""
    mod = _load()
    marker = tmp_path / "stub-ruff-ran"
    _make_exec(
        tmp_path / ".venv" / "bin" / "ruff",
        f"#!/bin/sh\ntouch {marker}\nexit 0\n",
    )
    knowledge = tmp_path / ".ucw" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "PREFERENCES.md").write_text("| linter | ruff |\n")
    result = mod.run_verification(tmp_path, timeout=10, only=["lint"])
    assert result["passed"] is True
    assert marker.exists(), "the .venv stub should have been executed"


def test_run_one_absent_binary_flagged_tooling_missing(clean_env, tmp_path):
    """A binary found nowhere (probes + PATH) is an environment problem —
    same class as 'command not found' in gate output."""
    mod = _load()
    res = mod._run_one(tmp_path, ["ucw-definitely-absent-tool-xyz"], timeout=5)
    assert res["passed"] is False
    assert res["tooling_missing"] is True
    assert "not found" in res["summary"].lower()


def test_absent_binary_setup_skips_off_strict(clean_env, tmp_path):
    """Suite-level: a detected gate whose binary is absent everywhere is a
    setup-skip (not a failure) outside strict conditions."""
    mod = _load()
    mod.LINT_COMMANDS["ruff"] = ["ucw-absent-lint-tool-xyz", "check"]
    knowledge = tmp_path / ".ucw" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "PREFERENCES.md").write_text("| linter | ruff |\n")
    result = mod.run_verification(tmp_path, timeout=10, only=["lint"])
    assert result["passed"] is True
    assert result["setup_skipped"] == ["lint"]


# ---- strict-gate detection --------------------------------------------------

def test_strict_gates_from_phase(clean_env, tmp_path):
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    for phase, expected in [("build", False), ("verify", True), ("land", True)]:
        (state / "phase").write_text(phase + "\n")
        assert mod.strict_gates(tmp_path) is expected, phase


def test_strict_gates_from_auto_level(clean_env, tmp_path):
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    for level, expected in [(1, False), (2, True), (3, True), (4, True)]:
        (state / "auto-mode").write_text(json.dumps({"level": level}))
        assert mod.strict_gates(tmp_path) is expected, f"level {level}"


def test_strict_gates_env_override(clean_env, tmp_path, monkeypatch):
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "phase").write_text("land\n")
    monkeypatch.setenv("UCW_VERIFY_STRICT", "0")
    assert mod.strict_gates(tmp_path) is False
    monkeypatch.setenv("UCW_VERIFY_STRICT", "1")
    (state / "phase").write_text("build\n")
    assert mod.strict_gates(tmp_path) is True


def test_auto_level_mirrors_hook_semantics(clean_env, tmp_path, monkeypatch):
    """_auto_level must not drift from hooks/_hook_common.auto_mode_level."""
    mod = _load()
    monkeypatch.setenv("UCW_AUTO_MODE", "on")
    assert mod._auto_level(tmp_path) == 4
    monkeypatch.setenv("UCW_AUTO_MODE", "off")
    assert mod._auto_level(tmp_path) == 0
    monkeypatch.setenv("UCW_AUTO_MODE", "3")
    assert mod._auto_level(tmp_path) == 3
    monkeypatch.delenv("UCW_AUTO_MODE")
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "auto-mode").write_text(json.dumps({"level": 2}))
    assert mod._auto_level(tmp_path) == 2
    (state / "auto-mode").write_text(json.dumps({"level": 7}))
    assert mod._auto_level(tmp_path) == 0


# ---- strict-mode behavior ---------------------------------------------------

def _missing_tool_makefile(project: Path, *, with_test: bool = True) -> None:
    body = f"lint:\n\t@echo '{MISSING_TOOL_LINE}' >&2; exit 1\n"
    if with_test:
        body += "test:\n\t@true\n"
    (project / "Makefile").write_text(body)


def test_strict_missing_tool_fails_gate_in_land_phase(clean_env, tmp_path):
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "phase").write_text("land\n")
    _missing_tool_makefile(tmp_path)
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["passed"] is False
    assert result["failed_gate"] == "lint"
    assert "install.sh --reinstall-deps" in result["summary"]
    lint = next(g for g in result["gates"] if g["name"] == "lint")
    assert lint.get("strict_tooling_failure") is True
    assert lint.get("setup_skipped") is not True


def test_strict_missing_tool_fails_gate_at_auto_level_2(clean_env, tmp_path):
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "auto-mode").write_text(json.dumps({"level": 2}))
    _missing_tool_makefile(tmp_path)
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["passed"] is False
    assert result["failed_gate"] == "lint"


def test_non_strict_setup_skip_preserved_in_build_phase(clean_env, tmp_path):
    """The transparent skip must survive untouched outside strict conditions."""
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "phase").write_text("build\n")
    _missing_tool_makefile(tmp_path)
    result = mod.run_verification(tmp_path, timeout=10)
    assert result["passed"] is True
    assert result["setup_skipped"] == ["lint"]
    assert result["failed_gate"] is None


def test_strict_auto_install_still_fixes_before_failing(clean_env, tmp_path):
    """Strict + auto_install: a successful install-and-retry beats failing."""
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "auto-mode").write_text(json.dumps({"level": 2}))
    (tmp_path / "Makefile").write_text(
        "install:\n\t@touch .deps-installed\n"
        f"lint:\n\t@test -f .deps-installed || (echo '{MISSING_TOOL_LINE}' >&2; exit 1)\n"
    )
    result = mod.run_verification(tmp_path, timeout=10, auto_install=True)
    assert result["passed"] is True
    assert result["setup_skipped"] == []


def test_cli_strict_flag_forces_failure(clean_env, tmp_path, capsys):
    mod = _load()
    _missing_tool_makefile(tmp_path)
    rc = mod.main(["--repo", str(tmp_path), "--timeout", "10", "--strict"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body["failed_gate"] == "lint"


def test_cli_no_strict_flag_forces_skip_even_at_land(clean_env, tmp_path, capsys):
    mod = _load()
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "phase").write_text("land\n")
    _missing_tool_makefile(tmp_path)
    rc = mod.main(["--repo", str(tmp_path), "--timeout", "10", "--no-strict"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "lint" in body["setup_skipped"]


# ---- verify-report + tree-keyed cache ---------------------------------------

def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=ucw@test", "-c", "user.name=ucw", *args],
        cwd=repo, check=True, capture_output=True, timeout=15,
    )


def _git_project(tmp_path: Path, makefile: str) -> Path:
    (tmp_path / "Makefile").write_text(makefile)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "Makefile")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path

COUNTING_MAKEFILE = "test:\n\t@echo run >> gate-runs.log\n"


def _run_count(project: Path) -> int:
    log = project / "gate-runs.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_cli_always_writes_verify_report(clean_env, tmp_path, capsys):
    mod = _load()
    (tmp_path / "Makefile").write_text("test:\n\t@true\n")
    assert mod.main(["--repo", str(tmp_path), "--timeout", "10"]) == 0
    capsys.readouterr()
    report = tmp_path / ".ucw" / "state" / "verify-report.json"
    assert report.exists()
    assert json.loads(report.read_text())["passed"] is True

    (tmp_path / "Makefile").write_text("test:\n\t@false\n")
    assert mod.main(["--repo", str(tmp_path), "--timeout", "10"]) == 1
    capsys.readouterr()
    assert json.loads(report.read_text())["passed"] is False


def test_no_cache_outside_git_repo(clean_env, tmp_path, capsys):
    mod = _load()
    (tmp_path / "Makefile").write_text(COUNTING_MAKEFILE)
    mod.main(["--repo", str(tmp_path), "--timeout", "10"])
    mod.main(["--repo", str(tmp_path), "--timeout", "10"])
    capsys.readouterr()
    assert _run_count(tmp_path) == 2, "no git → no cache → both runs execute"
    assert not (tmp_path / ".ucw" / "state" / "last-verify.json").exists()


def test_cache_hit_on_unchanged_tree(clean_env, tmp_path, capsys):
    mod = _load()
    project = _git_project(tmp_path, COUNTING_MAKEFILE)
    assert mod.main(["--repo", str(project), "--timeout", "10"]) == 0
    capsys.readouterr()
    cache = project / ".ucw" / "state" / "last-verify.json"
    assert cache.exists()
    assert ":" in json.loads(cache.read_text())["key"]

    rc = mod.main(["--repo", str(project), "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["cached"] is True
    assert "cached PASS" in body["summary"]
    assert _run_count(project) == 1, "cache hit must skip the re-run"
    # The report reflects the cached result too.
    report = json.loads((project / ".ucw" / "state" / "verify-report.json").read_text())
    assert report.get("cached") is True


def test_cache_miss_on_dirty_tree_and_new_commit(clean_env, tmp_path, capsys):
    mod = _load()
    project = _git_project(tmp_path, COUNTING_MAKEFILE)
    mod.main(["--repo", str(project), "--timeout", "10"])
    # Dirty tree: a tracked-file edit changes `git diff HEAD` → key changes.
    (project / "Makefile").write_text(COUNTING_MAKEFILE + "# touched\n")
    mod.main(["--repo", str(project), "--timeout", "10"])
    assert _run_count(project) == 2, "dirty tree must invalidate the cache"
    # New commit: HEAD sha changes → key changes again vs the refreshed cache.
    _git(project, "add", "Makefile")
    _git(project, "commit", "-q", "-m", "touch")
    mod.main(["--repo", str(project), "--timeout", "10"])
    capsys.readouterr()
    assert _run_count(project) == 3, "a new commit must invalidate the cache"


def test_no_cache_flag_forces_rerun(clean_env, tmp_path, capsys):
    mod = _load()
    project = _git_project(tmp_path, COUNTING_MAKEFILE)
    mod.main(["--repo", str(project), "--timeout", "10"])
    rc = mod.main(["--repo", str(project), "--timeout", "10", "--no-cache"])
    capsys.readouterr()
    assert rc == 0
    assert _run_count(project) == 2, "--no-cache must re-run the gates"


def test_failing_run_not_cached(clean_env, tmp_path, capsys):
    mod = _load()
    project = _git_project(tmp_path, "test:\n\t@false\n")
    assert mod.main(["--repo", str(project), "--timeout", "10"]) == 1
    assert mod.main(["--repo", str(project), "--timeout", "10"]) == 1
    capsys.readouterr()
    assert not (project / ".ucw" / "state" / "last-verify.json").exists()


def test_cached_setup_skip_pass_not_served_under_strict(clean_env, tmp_path, capsys):
    """A non-strict pass that setup-skipped a gate must NOT satisfy a later
    strict run of the same tree — under strict that skip is a failure."""
    mod = _load()
    project = _git_project(
        tmp_path,
        f"lint:\n\t@echo '{MISSING_TOOL_LINE}' >&2; exit 1\n"
        + COUNTING_MAKEFILE,
    )
    assert mod.main(["--repo", str(project), "--timeout", "10"]) == 0
    capsys.readouterr()
    assert (project / ".ucw" / "state" / "last-verify.json").exists()

    # Phase flip to land is untracked state — the tree key is unchanged, so
    # only the strict guard stands between a stale skip and a false pass.
    (project / ".ucw" / "state" / "phase").write_text("land\n")
    rc = mod.main(["--repo", str(project), "--timeout", "10"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body.get("cached") is not True
    assert body["failed_gate"] == "lint"
    assert "install.sh --reinstall-deps" in body["summary"]


# ---- stop hook: strict handling of skips ------------------------------------

def _stop(project: Path, env_extra: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{REPO_ROOT}/memory"
    for var in ("UCW_AUTO_MODE", "UCW_AUTO_RETRY_CAP", "UCW_SKIP_AUTO_VERIFY",
                "UCW_VERIFY_STRICT", "UCW_VERIFY_GATES"):
        env.pop(var, None)
    if env_extra:
        env.update(env_extra)
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "stop.py")],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True, text=True, env=env, timeout=20,
    )
    return cp.returncode, cp.stdout, cp.stderr


@pytest.fixture
def build_project(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("build")
    (tmp_path / ".ucw" / "state" / "edit-streak").write_text("3")
    return tmp_path


def _set_level(project: Path, level: int) -> None:
    (project / ".ucw" / "state" / "auto-mode").write_text(
        json.dumps({"level": level, "since": "2026-01-01T00:00:00Z"}))


def test_stop_level2_blocks_when_no_gates_detected(build_project):
    """Level >= 2 with zero detectable gates must block, not silently clear
    the streak — nobody is left to notice the skipped suite."""
    _set_level(build_project, 2)
    _rc, out, _ = _stop(build_project)
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "install.sh --reinstall-deps" in body["reason"]
    assert (build_project / ".ucw" / "state" / "edit-streak").read_text().strip() == "3"


def test_stop_level0_still_allows_when_no_gates_detected(build_project):
    _rc, out, _ = _stop(build_project)
    assert out == ""
    assert not (build_project / ".ucw" / "state" / "edit-streak").exists()


def test_stop_level2_blocks_on_missing_tool(build_project):
    """ucw-verify strict-fails the missing-tool gate at level >= 2, so the
    hook blocks with the install hint instead of setup-skip success."""
    _set_level(build_project, 2)
    (build_project / "Makefile").write_text(
        f"lint:\n\t@echo '{MISSING_TOOL_LINE}' >&2; exit 1\n"
        "test:\n\t@true\n"
    )
    _rc, out, _ = _stop(build_project)
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "install.sh --reinstall-deps" in body["reason"]
    phase = (build_project / ".ucw" / "state" / "phase").read_text().strip()
    assert phase == "build", "phase must not advance on a strict gate failure"


def test_stop_level0_missing_tool_setup_skip_still_allows(build_project):
    """Non-strict (level 0): missing tooling remains a transparent skip —
    passing tests clear the streak and advance the phase."""
    (build_project / "Makefile").write_text(
        f"lint:\n\t@echo '{MISSING_TOOL_LINE}' >&2; exit 1\n"
        "test:\n\t@true\n"
    )
    _rc, out, _ = _stop(build_project)
    assert out == ""
    phase = (build_project / ".ucw" / "state" / "phase").read_text().strip()
    assert phase == "verify"


def test_stop_passing_run_records_cache_for_ship(build_project):
    """Contract: a passing Stop-hook verify records the tree key so the
    /ucw ship that follows is a cache hit."""
    _git_project(build_project, "test:\n\t@true\n")
    _rc, out, _ = _stop(build_project)
    assert out == ""
    state = build_project / ".ucw" / "state"
    assert (state / "verify-report.json").exists()
    assert (state / "last-verify.json").exists()
    # The follow-up verify (what ship runs) hits the cache.
    cp = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "ucw-verify.py"),
         "--repo", str(build_project)],
        capture_output=True, text=True, timeout=30,
        env={k: v for k, v in os.environ.items()
             if k not in {"UCW_VERIFY_GATES", "UCW_VERIFY_STRICT", "UCW_AUTO_MODE"}},
    )
    body = json.loads(cp.stdout)
    assert cp.returncode == 0
    assert body.get("cached") is True
    assert "cached PASS" in body["summary"]


# ---- Makefile + install.sh (shell — grep-style assertions) -------------------

def test_makefile_lint_labels_skips():
    text = (REPO_ROOT / "Makefile").read_text()
    assert "SKIPPED (ruff not installed)" in text
    assert "SKIPPED (shellcheck not installed)" in text
    assert "(ruff not installed — skipping)" not in text


def test_makefile_validate_qualifies_green_when_gates_skipped():
    text = (REPO_ROOT / "Makefile").read_text()
    assert "gates skipped" in text
    assert "all green" in text


def test_install_sh_provisions_gate_tools():
    text = (REPO_ROOT / "install.sh").read_text()
    assert "install_gate_tools" in text
    assert "pytest ruff" in text
    # Idempotency guard: skip when the tools are already importable/present.
    assert 'import pytest' in text
    # Wired into the profile install flow after the venv is set up.
    assert text.index("install_memory_deps\n") < text.index("install_gate_tools\n")
