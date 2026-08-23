#!/usr/bin/env python3
"""Project-aware verification runner — runs the full gate suite (lint, types, tests).

Used by:
- `hooks/stop.py` for auto-verify-on-Stop (its primary caller)
- The `verifier` subagent's gate suite (callable as part of /ucw ship)
- Direct invocation: `ucw-verify.py` from any project root

Gates (run in order, stop at first failure unless --all):
1. **lint**  — formatter/linter (eslint, biome, ruff, golangci-lint, clippy, ...)
2. **types** — typechecker (tsc, mypy, cargo check, go vet, ...)
3. **tests** — test runner (pytest, vitest, jest, go test, ...)

Per-gate detection (first match wins):
- `.ucw/knowledge/PREFERENCES.md` field (`linter`, `typechecker`, `test_runner`)
- `make <gate>` if Makefile has a target named like the gate
- package.json `scripts.<gate>` (npm/pnpm/yarn/bun run <gate>)
- Stack-detected default (see RUNNER_COMMANDS / LINT_COMMANDS / TYPE_COMMANDS)
- Else: gate skipped (transparent — doesn't fail the suite)

Behavior:
- Each gate gets its own wall-clock timeout (default 60s, env UCW_VERIFY_TIMEOUT
  applies to the WHOLE suite as an outer cap; individual gates share the budget).
- Captures combined stdout+stderr (truncated to 2 KB for the report)
- Top-level JSON keeps back-compat with the single-gate shape: `command`,
  `summary`, `exit_code`, `elapsed_ms`, `source` point at the failing gate
  (or the last run gate when everything passes). Adds `gates: [...]` with
  per-gate detail and `failed_gate: "lint"|"types"|"tests"|null`.
- `UCW_VERIFY_GATES=tests` (comma-separated) limits which gates run — useful
  to opt out of lint/types if a project intentionally skips them.
- Missing tooling vs. real failure: a gate exiting non-zero because its
  binary/deps aren't installed (e.g. `eslint: not found`, `node_modules
  missing`) is NOT a gate failure — there are no errors to fix. With
  `--auto-install` the project's install command (`make install`, or
  `<pm> install` from a lockfile) runs once and the gate is retried; without
  it the gate is setup-skipped (passed, with a `setup_hint`) so the agent
  isn't told to fix nonexistent lint errors. `setup_skipped`/`setup_hint`
  appear at the top level when this happens.
- Tool resolution probes project-local installs BEFORE PATH:
  `<repo>/.venv/bin`, `<repo>/venv/bin`, `<repo>/node_modules/.bin`,
  `$UCW_HOME/venv/bin` (where install.sh provisions pytest + ruff as a
  fallback), then `shutil.which` on PATH. The same dirs are prepended to the
  gate subprocess PATH so indirect invocations (`make lint` → ruff) resolve
  identically.
- Strict gates: when the workflow phase is `verify` or `land`, or auto-mode
  level >= 2, a setup-skip (missing tool) is a gate FAILURE — nobody is left
  in the loop to notice a silently skipped gate. `--strict` / `--no-strict`
  (or UCW_VERIFY_STRICT=1/0) override the auto-detection.
- Reporting + caching: the JSON result is always written to
  `.ucw/state/verify-report.json`. A tree-keyed cache lives at
  `.ucw/state/last-verify.json` (key = HEAD sha + sha256 of `git diff HEAD`
  output); an unchanged tree since the last PASSING run skips the re-run and
  reports "cached PASS". `--no-cache` forces a fresh run.

Exit codes (CLI):
- 0 if all (non-skipped) gates pass
- 1 if any gate fails
- 2 if any gate times out
- 3 if no gate had a command to run (everything skipped)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

GATE_ORDER = ("lint", "types", "tests")

# Per-runner command tables. Keys are runner names as they appear in
# PREFERENCES.md or from ucw-detect-stack.py output.
RUNNER_COMMANDS: dict[str, list[str]] = {
    "pytest":      ["pytest", "-q", "--no-header"],
    "vitest":      ["vitest", "run", "--reporter=dot"],
    "jest":        ["jest", "--silent"],
    "mocha":       ["npx", "mocha"],
    "playwright":  ["npx", "playwright", "test", "--reporter=dot"],
    "go test":     ["go", "test", "./..."],
    "cargo test":  ["cargo", "test", "--quiet"],
    "rspec":       ["rspec", "--format", "progress"],
}

LINT_COMMANDS: dict[str, list[str]] = {
    "eslint":         ["npx", "eslint", "."],
    "biome":          ["npx", "biome", "check", "."],
    "ruff":           ["ruff", "check", "."],
    "golangci-lint":  ["golangci-lint", "run"],
    "clippy":         ["cargo", "clippy", "--quiet", "--", "-D", "warnings"],
}

TYPE_COMMANDS: dict[str, list[str]] = {
    "tsc":          ["npx", "tsc", "--noEmit"],
    "mypy":         ["mypy", "."],
    "cargo check":  ["cargo", "check", "--quiet"],
    "go vet":       ["go", "vet", "./..."],
}


# ---- helpers --------------------------------------------------------------

def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load_json(path: Path) -> dict[str, Any] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def _preferences_field(project_root: Path, field: str) -> str | None:
    """Read a named field from .ucw/knowledge/PREFERENCES.md.

    Matches both table rows (`| field | value |`) and key:value form.
    """
    prefs = project_root / ".ucw" / "knowledge" / "PREFERENCES.md"
    text = _read_text(prefs)
    if not text:
        return None
    pattern = rf"{re.escape(field)}\s*[|:=]\s*([^\n|]+?)(?:\s*\||\s*$)"
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip().strip("`*_ ")
    # Reject template placeholders like {{linter}} that the onboarder left blank.
    if not value or value.startswith("{{"):
        return None
    return value.lower()


def _has_makefile_target(project_root: Path, target: str) -> bool:
    text = _read_text(project_root / "Makefile")
    if text is None:
        return False
    return bool(re.search(rf"^{re.escape(target)}\s*:", text, re.MULTILINE))


def _package_manager(project_root: Path) -> str:
    """Detect the JS package manager from lockfiles. Default: npm."""
    if (project_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_root / "yarn.lock").exists():
        return "yarn"
    if (project_root / "bun.lockb").exists() or (project_root / "bun.lock").exists():
        return "bun"
    return "npm"


def _package_json_script(project_root: Path, name: str) -> list[str] | None:
    """If package.json has a script named `name`, return the run command."""
    pkg = _load_json(project_root / "package.json")
    if pkg is None:
        return None
    scripts = pkg.get("scripts") or {}
    if name not in scripts:
        return None
    pm = _package_manager(project_root)
    return [pm, "run", name]


def _detect_stack(project_root: Path) -> dict[str, Any] | None:
    """Run ucw-detect-stack.py and return its JSON, or None."""
    helper = REPO_ROOT / "bin" / "ucw-detect-stack.py"
    if not helper.exists():
        for cand in [Path.home() / ".claude" / "ucw" / "bin" / "ucw-detect-stack.py"]:
            if cand.exists():
                helper = cand
                break
        else:
            return None
    try:
        cp = subprocess.run(
            [sys.executable, str(helper), str(project_root)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout or "{}")
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# ---- tool resolution -------------------------------------------------------
# Bare `shutil.which` misses project-local installs (a repo venv, node_modules
# binaries) and the UCW fallback venv that install.sh provisions with pytest +
# ruff. Probe those first so gates find their tooling even under a minimal
# hook PATH; PATH stays the last resort.

def _ucw_home() -> Path:
    env = os.environ.get("UCW_HOME", "").strip()
    return Path(env) if env else Path.home() / ".claude" / "ucw"


def _tool_probe_dirs(project_root: Path) -> list[Path]:
    """Directories probed (in order) before falling back to PATH."""
    return [
        project_root / ".venv" / "bin",
        project_root / "venv" / "bin",
        project_root / "node_modules" / ".bin",
        _ucw_home() / "venv" / "bin",
    ]


def _resolve_tool(project_root: Path, bin_name: str) -> str | None:
    """Resolve a gate binary: probe dirs first, then PATH. None if absent."""
    if os.sep in bin_name:
        cand = (project_root / bin_name).resolve()
        return str(cand) if cand.is_file() and os.access(cand, os.X_OK) else None
    for d in _tool_probe_dirs(project_root):
        cand = d / bin_name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which(bin_name)


def _gate_env(project_root: Path) -> dict[str, str]:
    """Subprocess env with existing probe dirs prepended to PATH so tools
    spawned indirectly (`make lint` → ruff, npx shims) resolve the same way
    the gate binary itself did."""
    env = os.environ.copy()
    prefix = [str(d) for d in _tool_probe_dirs(project_root) if d.is_dir()]
    if prefix:
        env["PATH"] = os.pathsep.join([*prefix, env.get("PATH", "")])
    return env


# ---- strict gates ----------------------------------------------------------
# In Build a missing tool is a transparent setup-skip (there are no errors to
# fix). At verify/land — or with auto-mode driving (level >= 2) — nobody is
# left in the loop to notice a silently skipped gate before code ships, so a
# missing tool becomes a FAILURE with an actionable install hint.

STRICT_PHASES = ("verify", "land")

_VALID_AUTO_LEVELS = (1, 2, 3, 4)


def _workflow_phase(project_root: Path) -> str | None:
    text = _read_text(project_root / ".ucw" / "state" / "phase")
    return (text or "").strip() or None


def _auto_level(project_root: Path) -> int:
    """Auto-mode level (0 = off): UCW_AUTO_MODE env → .ucw/state/auto-mode → 0.

    Mirrors hooks/_hook_common.auto_mode_level — bin/ CLIs can't import from
    hooks/ (and hooks stay self-contained); tests guard against drift.
    """
    env = os.environ.get("UCW_AUTO_MODE", "").strip().lower()
    if env in {"0", "off", "no", "false"}:
        return 0
    if env.isdigit() and int(env) in _VALID_AUTO_LEVELS:
        return int(env)
    if env == "on":
        return 4
    data = _load_json(project_root / ".ucw" / "state" / "auto-mode")
    level = (data or {}).get("level")
    if isinstance(level, int) and level in _VALID_AUTO_LEVELS:
        return level
    return 0


def strict_gates(project_root: Path) -> bool:
    """True when a setup-skip (missing tool) must be a gate FAILURE.

    UCW_VERIFY_STRICT=1/0 forces it either way; otherwise strict when the
    workflow phase is verify/land or auto-mode level >= 2.
    """
    env = os.environ.get("UCW_VERIFY_STRICT", "").strip().lower()
    if env in {"1", "true", "yes"}:
        return True
    if env in {"0", "false", "no"}:
        return False
    return _workflow_phase(project_root) in STRICT_PHASES or _auto_level(project_root) >= 2


# ---- missing-tooling detection + install ----------------------------------
# A gate exiting non-zero means one of two very different things: the tool RAN
# and found problems (the agent should fix them), or the tool ISN'T INSTALLED
# (an environment problem — there are no lint errors to fix). These patterns
# identify the latter so we don't tell the agent to "fix the lint failures"
# when `node_modules` is simply missing. Conservative on purpose: a bare
# `ELIFECYCLE`/`exit code 1` is NOT enough (that's how a real lint failure
# exits too) — we require positive evidence the binary/module is absent.
_MISSING_TOOLING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sh: \d+: [^:]+: not found", re.MULTILINE),    # POSIX sh
    re.compile(r"\bcommand not found\b"),                       # bash/zsh
    re.compile(r"\bnode_modules missing\b"),                    # pnpm warn
    re.compile(r"\bis not recognized as an internal or external command\b"),  # win
    re.compile(r"\bnpm ERR!.*\benoent\b", re.IGNORECASE),       # npm missing
    re.compile(r"\bexecutable not found\b", re.IGNORECASE),
)
# NOTE: deliberately NOT matching "Cannot find module" — a broken import in
# app/test code emits that too, and we must not silently skip a real failure.
# `node_modules missing` / `command not found` already cover genuinely-absent
# deps without that overlap.


def _looks_like_missing_tooling(output: str) -> bool:
    """True if a gate's output indicates the tool/deps aren't installed
    (vs. the tool running and reporting real problems)."""
    return any(p.search(output or "") for p in _MISSING_TOOLING_PATTERNS)


def _install_command(project_root: Path) -> list[str] | None:
    """Best-effort command to install the project's dependencies.

    Makefile `install` target wins (explicit project intent); otherwise the JS
    package manager's install from a detected lockfile. Returns None when we
    can't safely guess — we never invent an install command.
    """
    if _has_makefile_target(project_root, "install"):
        return ["make", "install"]
    if (project_root / "package.json").exists() and any(
        (project_root / lock).exists()
        for lock in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json",
                     "bun.lockb", "bun.lock")
    ):
        return [_package_manager(project_root), "install"]
    return None


# ---- per-gate selection ---------------------------------------------------

def _select_lint(project_root: Path, stack: dict[str, Any] | None) -> tuple[list[str], str] | None:
    pref = _preferences_field(project_root, "linter")
    if pref and pref in LINT_COMMANDS:
        return LINT_COMMANDS[pref], f"preferences:{pref}"

    if _has_makefile_target(project_root, "lint"):
        return ["make", "lint"], "makefile:lint"

    script = _package_json_script(project_root, "lint")
    if script:
        return script, "package.json:lint"

    linters = (stack or {}).get("linters") or []
    # priority: eslint > biome > ruff > golangci-lint > clippy
    for name in ("eslint", "biome", "ruff", "golangci-lint", "clippy"):
        if name in linters and name in LINT_COMMANDS:
            return LINT_COMMANDS[name], f"detected:{name}"
    return None


def _select_types(project_root: Path, stack: dict[str, Any] | None) -> tuple[list[str], str] | None:
    pref = _preferences_field(project_root, "typechecker")
    if pref and pref in TYPE_COMMANDS:
        return TYPE_COMMANDS[pref], f"preferences:{pref}"

    for target in ("typecheck", "types"):
        if _has_makefile_target(project_root, target):
            return ["make", target], f"makefile:{target}"

    for script_name in ("typecheck", "tsc"):
        script = _package_json_script(project_root, script_name)
        if script:
            return script, f"package.json:{script_name}"

    # Stack-based defaults
    languages = (stack or {}).get("languages") or []
    linters = (stack or {}).get("linters") or []

    if (project_root / "tsconfig.json").exists() or "typescript" in languages:
        return TYPE_COMMANDS["tsc"], "detected:tsc"
    if "mypy" in linters:
        return TYPE_COMMANDS["mypy"], "detected:mypy"
    if "rust" in languages:
        return TYPE_COMMANDS["cargo check"], "detected:cargo check"
    if "go" in languages:
        return TYPE_COMMANDS["go vet"], "detected:go vet"
    return None


def _select_tests(project_root: Path, stack: dict[str, Any] | None) -> tuple[list[str], str] | None:
    pref = _preferences_field(project_root, "test_runner")
    if pref and pref in RUNNER_COMMANDS:
        return RUNNER_COMMANDS[pref], f"preferences:{pref}"

    if _has_makefile_target(project_root, "test"):
        return ["make", "test"], "makefile:test"

    script = _package_json_script(project_root, "test")
    if script:
        return script, "package.json:test"

    runners = (stack or {}).get("test_runners") or []
    priority = ["pytest", "vitest", "jest", "go", "cargo", "mocha", "rspec", "playwright"]
    for p in priority:
        for r in runners:
            if p in r and r in RUNNER_COMMANDS:
                return RUNNER_COMMANDS[r], f"detected:{r}"
    return None


def select_command(project_root: Path) -> tuple[list[str], str] | None:
    """Back-compat shim: returns the tests command only (used by older callers).

    New code should call `select_gates()` to get the full lint/types/tests plan.
    """
    return _select_tests(project_root, _detect_stack(project_root))


def select_gates(
    project_root: Path,
    *,
    only: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the plan: an ordered list of {name, command, source} dicts.

    Gates with no detected command are omitted (they're skipped transparently).
    `only` restricts to a subset (e.g. ["tests"]).
    """
    stack = _detect_stack(project_root)
    selectors = {
        "lint":  _select_lint,
        "types": _select_types,
        "tests": _select_tests,
    }
    requested = [g for g in GATE_ORDER if (only is None or g in only)]
    plan: list[dict[str, Any]] = []
    for gate in requested:
        chosen = selectors[gate](project_root, stack)
        if chosen is None:
            continue
        command, source = chosen
        plan.append({"name": gate, "command": command, "source": source})
    return plan


# ---- runner ---------------------------------------------------------------

def _run_one(
    project_root: Path, command: list[str], *, timeout: int,
) -> dict[str, Any]:
    """Run a single command. Returns the per-gate result dict (no `name` yet)."""
    bin_name = command[0]
    resolved = _resolve_tool(project_root, bin_name)
    if resolved is None:
        return {
            "passed": False,
            "command": " ".join(command),
            "exit_code": None,
            "elapsed_ms": 0,
            "timed_out": False,
            # The binary is absent everywhere we probed — that's the same
            # environment problem as a "command not found" in gate output, so
            # flag it the same way (setup-skip off strict, FAILURE under it).
            "tooling_missing": True,
            "summary": (
                f"runner not found: {bin_name!r} (probed project .venv/venv, "
                f"node_modules/.bin, $UCW_HOME/venv, then PATH)"
            ),
        }

    start = time.perf_counter()
    try:
        cp = subprocess.run(
            [resolved, *command[1:]],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_gate_env(project_root),
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "passed": False,
            "command": " ".join(command),
            "exit_code": None,
            "elapsed_ms": elapsed_ms,
            "timed_out": True,
            "summary": f"timed out after {timeout}s — set UCW_VERIFY_TIMEOUT or skip via UCW_SKIP_AUTO_VERIFY=1",
        }

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    combined = (cp.stdout or "") + (cp.stderr or "")
    tail = combined.strip()[-2000:]
    return {
        "passed": cp.returncode == 0,
        "command": " ".join(command),
        "exit_code": cp.returncode,
        "elapsed_ms": elapsed_ms,
        "timed_out": False,
        # A non-zero exit whose output says the binary/module is missing is an
        # environment problem, not a gate failure — flag it so the caller can
        # install + retry (auto-mode) or skip with a hint instead of telling
        # the agent to "fix the lint failures" that don't exist.
        "tooling_missing": cp.returncode != 0 and _looks_like_missing_tooling(combined),
        "summary": tail or ("(no output)" if cp.returncode == 0 else "non-zero exit, no output"),
    }


def _skipped_result(reason: str) -> dict[str, Any]:
    return {
        "passed": True,
        "skipped": True,
        "command": None,
        "exit_code": None,
        "elapsed_ms": 0,
        "timed_out": False,
        "summary": reason,
        "source": "skipped",
        "gates": [],
        "failed_gate": None,
    }


def _last_gate_summary(gates: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the gate whose detail should be exposed at the top level.

    Failing gate wins (for the failure path). Otherwise the last gate that
    actually ran.
    """
    for g in gates:
        if not g.get("passed"):
            return g
    return gates[-1]


def _setup_skip(result: dict[str, Any], gate_name: str,
                install_cmd: list[str] | None) -> dict[str, Any]:
    """Convert a 'tooling not installed' gate result into a transparent skip
    (passed, doesn't fail the suite) carrying an actionable hint."""
    hint = (
        f"{gate_name} gate skipped — its tooling isn't installed "
        f"(`{result.get('command')}` reported a missing binary/module, not "
        f"lint/type errors). "
    )
    hint += (f"Run `{' '.join(install_cmd)}` to enable it."
             if install_cmd else
             "Install the project's dependencies to enable it.")
    result.update({
        "passed": True,
        "skipped": True,
        "setup_skipped": True,
        "setup_hint": hint,
    })
    return result


def _strict_fail(result: dict[str, Any], gate_name: str,
                 install_cmd: list[str] | None) -> dict[str, Any]:
    """Under strict gates a missing tool is a FAILURE, not a skip: at
    verify/land (or with auto-mode driving) nobody would notice the skip
    before code ships."""
    detail = (f"Run `{' '.join(install_cmd)}` (or install.sh --reinstall-deps), "
              if install_cmd else
              "Install it (or run install.sh --reinstall-deps), ")
    original = (result.get("summary") or "").strip()
    result.update({
        "passed": False,
        "strict_tooling_failure": True,
        "summary": (
            f"{gate_name} gate FAILED (strict): tool missing — install or run "
            f"install.sh --reinstall-deps. {detail}then re-run verify."
            + (f"\n\nOriginal output:\n{original}" if original else "")
        )[:2000],
    })
    return result


def run_verification(
    project_root: Path,
    *,
    timeout: int,
    command: list[str] | None = None,
    only: list[str] | None = None,
    run_all: bool = False,
    auto_install: bool = False,
    install_timeout: int = 300,
    strict: bool | None = None,
) -> dict[str, Any]:
    """Run the gate suite (or a single explicit command).

    `command` short-circuits gate detection — single-command back-compat.
    `only` restricts to a subset of gates (e.g. ["tests"]).
    `run_all` continues past the first failure instead of stopping.
    `auto_install` — when a gate fails because its tooling isn't installed,
    run the project's install command ONCE and retry that gate (used in
    auto-mode). Otherwise such a gate is setup-skipped, not failed.
    `strict` — treat a missing tool as a gate FAILURE instead of a
    setup-skip. None (default) auto-detects via `strict_gates()`: phase
    verify/land or auto-mode level >= 2.
    """
    if strict is None:
        strict = strict_gates(project_root)
    if command is not None:
        single = _run_one(project_root, command, timeout=timeout)
        single["source"] = "explicit"
        gate_record = {"name": "explicit", **single}
        return {
            **single,
            "gates": [gate_record],
            "failed_gate": None if single["passed"] else "explicit",
        }

    plan = select_gates(project_root, only=only)
    if not plan:
        return _skipped_result(
            "no gates detected — configure linter/typechecker/test_runner in "
            ".ucw/knowledge/PREFERENCES.md, add make targets, or add package.json scripts"
        )

    install_cmd = _install_command(project_root)
    installed = False  # run the install command at most once per suite
    gates: list[dict[str, Any]] = []
    for step in plan:
        result = _run_one(project_root, step["command"], timeout=timeout)
        result["name"] = step["name"]
        result["source"] = step["source"]

        if result.get("tooling_missing"):
            # Tool/deps not installed. On auto-mode, install once and retry
            # this gate; otherwise skip it (don't nag about errors that the
            # tool never actually got to report).
            if auto_install and not installed and install_cmd:
                inst = _run_one(project_root, install_cmd, timeout=install_timeout)
                installed = True
                result["install"] = {
                    "command": inst.get("command"),
                    "passed": inst.get("passed"),
                    "summary": inst.get("summary", "")[-500:],
                }
                if inst.get("passed"):
                    retry = _run_one(project_root, step["command"], timeout=timeout)
                    retry["name"] = step["name"]
                    retry["source"] = step["source"]
                    retry["install"] = result["install"]
                    result = retry
            # If still missing (no auto-install, install unavailable, install
            # failed, or retry still can't find the tool) → setup-skip, or a
            # hard failure when strict gates are in force.
            if result.get("tooling_missing"):
                if strict:
                    _strict_fail(result, step["name"], install_cmd)
                else:
                    _setup_skip(result, step["name"], install_cmd)

        gates.append(result)
        if not result["passed"] and not run_all:
            break

    top = _last_gate_summary(gates)
    failed = next((g["name"] for g in gates if not g["passed"]), None)
    setup_skipped = [g["name"] for g in gates if g.get("setup_skipped")]
    setup_hint = next((g.get("setup_hint") for g in gates if g.get("setup_skipped")), None)
    return {
        "passed": all(g["passed"] for g in gates),
        "command": top.get("command"),
        "exit_code": top.get("exit_code"),
        "elapsed_ms": top.get("elapsed_ms", 0),
        "timed_out": top.get("timed_out", False),
        "summary": top.get("summary", ""),
        "source": top.get("source", ""),
        "gates": gates,
        "failed_gate": failed,
        "setup_skipped": setup_skipped,
        "setup_hint": setup_hint,
    }


# ---- report + tree-keyed cache ---------------------------------------------
# Every CLI run persists its JSON result to .ucw/state/verify-report.json so
# other tools (reviewer lanes, /ucw ship) read one canonical artifact. A
# passing full-suite run is additionally cached at .ucw/state/last-verify.json
# keyed by the working tree (HEAD sha + sha256 of `git diff HEAD` output);
# re-verifying an unchanged tree — e.g. /ucw ship right after a passing
# Stop-hook verify — is a cache hit that skips the re-run ("cached PASS").

REPORT_FILE = Path(".ucw") / "state" / "verify-report.json"
CACHE_FILE = Path(".ucw") / "state" / "last-verify.json"


def _git(project_root: Path, *args: str) -> str | None:
    try:
        cp = subprocess.run(
            ["git", *args], cwd=project_root,
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def tree_key(project_root: Path) -> str | None:
    """Cache key for the current working tree: HEAD sha + sha256 of the
    uncommitted diff. None outside a git repo (no caching there).

    Note: untracked files don't appear in `git diff HEAD` — that's the agreed
    key contract; `--no-cache` covers the rare case where it matters.
    """
    head = _git(project_root, "rev-parse", "HEAD")
    if head is None:
        return None
    diff = _git(project_root, "diff", "HEAD")
    if diff is None:
        return None
    digest = hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()
    return f"{head.strip()}:{digest}"


def write_report(project_root: Path, result: dict[str, Any]) -> None:
    """Persist the run's JSON result to .ucw/state/verify-report.json (always)."""
    path = project_root / REPORT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_cached_pass(
    project_root: Path, *, only: list[str] | None, strict: bool,
) -> dict[str, Any] | None:
    """Return the cached result when the tree is unchanged since the last
    passing run of the same gate subset; else None.

    A strict run refuses a cached pass that setup-skipped any gate — under
    strict those skips would have been failures.
    """
    key = tree_key(project_root)
    if key is None:
        return None
    cache = _load_json(project_root / CACHE_FILE)
    if not cache or cache.get("key") != key:
        return None
    if cache.get("gates_only") != (only or "all"):
        return None
    result = cache.get("result")
    if not isinstance(result, dict) or not result.get("passed") or result.get("skipped"):
        return None
    if strict and result.get("setup_skipped"):
        return None
    return result


def record_cache(
    project_root: Path, result: dict[str, Any], *, only: list[str] | None,
) -> None:
    """Record a passing full-suite run keyed by the current tree."""
    if not result.get("passed") or result.get("skipped"):
        return
    key = tree_key(project_root)
    if key is None:
        return
    path = project_root / CACHE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "key": key,
            "gates_only": only or "all",
            "recorded_at": int(time.time()),
            "result": result,
        }, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---- CLI ------------------------------------------------------------------

def _default_timeout() -> int:
    raw = os.environ.get("UCW_VERIFY_TIMEOUT")
    if raw and raw.isdigit():
        return int(raw)
    return 60


def _gates_from_env() -> list[str] | None:
    raw = os.environ.get("UCW_VERIFY_GATES", "").strip()
    if not raw:
        return None
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    valid = [n for n in names if n in GATE_ORDER]
    return valid or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ucw-verify",
        description="Run the project's lint/types/tests gate suite with a timeout, report JSON.",
    )
    parser.add_argument("--repo", default=os.getcwd(),
                        help="project root (default: cwd)")
    parser.add_argument("--timeout", type=int, default=_default_timeout(),
                        help="per-gate wall-clock timeout in seconds (default: 60, env UCW_VERIFY_TIMEOUT)")
    parser.add_argument("--command", help="explicit single command (overrides gate detection)")
    parser.add_argument("--gates", help="comma-separated subset: lint,types,tests (env UCW_VERIFY_GATES)")
    parser.add_argument("--all", action="store_true",
                        help="run every gate even after a failure (default: stop at first failure)")
    parser.add_argument("--auto-install", action="store_true",
                        help="if a gate's tooling isn't installed, run the project's install "
                             "command once and retry (else such a gate is skipped, not failed)")
    parser.add_argument("--strict", dest="strict", action="store_true", default=None,
                        help="treat a missing tool as a gate FAILURE (auto-detected from "
                             "phase verify/land or auto-mode level >= 2 when omitted)")
    parser.add_argument("--no-strict", dest="strict", action="store_false",
                        help="force non-strict: missing tooling setup-skips even at verify/land")
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore .ucw/state/last-verify.json and re-run every gate")
    parser.add_argument("--json", action="store_true", help="emit JSON (default behavior)")
    args = parser.parse_args(argv)

    project_root = Path(args.repo).resolve()
    command = args.command.split() if args.command else None
    only = None
    if args.gates:
        names = [n.strip().lower() for n in args.gates.split(",") if n.strip()]
        only = [n for n in names if n in GATE_ORDER] or None
    if only is None:
        only = _gates_from_env()
    strict = strict_gates(project_root) if args.strict is None else args.strict

    if command is None and not args.no_cache:
        cached = load_cached_pass(project_root, only=only, strict=strict)
        if cached is not None:
            result = {
                **cached,
                "cached": True,
                "summary": ("cached PASS — tree unchanged since the last passing "
                            "verify (--no-cache forces a re-run)"),
            }
            write_report(project_root, result)
            json.dump(result, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0

    result = run_verification(
        project_root, timeout=args.timeout, command=command,
        only=only, run_all=args.all, auto_install=args.auto_install,
        strict=strict,
    )
    write_report(project_root, result)
    if command is None:
        record_cache(project_root, result, only=only)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if result.get("skipped"):
        return 3
    if result.get("timed_out"):
        return 2
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
