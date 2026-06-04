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

Exit codes (CLI):
- 0 if all (non-skipped) gates pass
- 1 if any gate fails
- 2 if any gate times out
- 3 if no gate had a command to run (everything skipped)
"""
from __future__ import annotations

import argparse
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
    if not shutil.which(bin_name):
        return {
            "passed": False,
            "command": " ".join(command),
            "exit_code": None,
            "elapsed_ms": 0,
            "timed_out": False,
            "summary": f"runner not found on PATH: {bin_name!r}",
        }

    start = time.perf_counter()
    try:
        cp = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
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


def run_verification(
    project_root: Path,
    *,
    timeout: int,
    command: list[str] | None = None,
    only: list[str] | None = None,
    run_all: bool = False,
) -> dict[str, Any]:
    """Run the gate suite (or a single explicit command).

    `command` short-circuits gate detection — single-command back-compat.
    `only` restricts to a subset of gates (e.g. ["tests"]).
    `run_all` continues past the first failure instead of stopping.
    """
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

    gates: list[dict[str, Any]] = []
    for step in plan:
        result = _run_one(project_root, step["command"], timeout=timeout)
        result["name"] = step["name"]
        result["source"] = step["source"]
        gates.append(result)
        if not result["passed"] and not run_all:
            break

    top = _last_gate_summary(gates)
    failed = next((g["name"] for g in gates if not g["passed"]), None)
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
    }


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

    result = run_verification(
        project_root, timeout=args.timeout, command=command,
        only=only, run_all=args.all,
    )
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
