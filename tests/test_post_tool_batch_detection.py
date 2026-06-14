"""Detection-shape coverage for the PostToolBatch streak breaker.

Bug fix (issue #8): the old implementation assumed
`payload["tools"][i].tool_input.command` and missed every other plausible
payload shape Claude Code might emit. The new detector walks the entire
payload tree, so any shape that includes a test command string anywhere
should reset the streak — and shape variations that don't include one
should leave the streak alone.

This module exists alongside `test_hooks.py` and `test_hook_schema_compliance.py`
specifically to lock in INPUT-shape compliance, which PR #6 only covered for
OUTPUT.
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


def _run(payload: dict, extra_env: dict | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    if extra_env:
        env.update(extra_env)
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "post-tool-batch.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=15,
    )
    return cp.returncode, cp.stdout, cp.stderr


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "edit-streak").write_text("3")
    return tmp_path


def _streak(project) -> str:
    sf = project / ".ucw" / "state" / "edit-streak"
    return sf.read_text().strip() if sf.exists() else "0"


# ---- payload SHAPES that should COUNT as a test run ------------------------
# Each of these represents a plausible PostToolBatch payload variant. The
# detector must spot the test command and reset the streak.

POSITIVE_SHAPES = [
    pytest.param(
        {"tools": [{"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}]},
        id="legacy-shape-tools-tool_input-command",
    ),
    pytest.param(
        {"batch": [{"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}]},
        id="batch-key-instead-of-tools",
    ),
    pytest.param(
        {"tool_calls": [{"name": "Bash", "input": {"command": "vitest run"}}]},
        id="tool_calls-name-input-shape",
    ),
    pytest.param(
        {"results": [{"tool": {"name": "Bash"},
                      "args": {"command": "go test ./..."}}]},
        id="results-tool-args-shape",
    ),
    pytest.param(
        {"tools": [
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/a.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "make test"}},
        ]},
        id="mixed-batch-with-test-at-the-end",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "npm test --silent"}}]},
        id="npm-test",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm run test"}}]},
        id="pnpm-run-test",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "yarn test"}}]},
        id="yarn-test",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "bun test"}}]},
        id="bun-test",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "cargo test --release"}}]},
        id="cargo-test",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "npx playwright test"}}]},
        id="playwright-test",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "bundle exec rspec spec/"}}]},
        id="rspec",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "phpunit --testdox"}}]},
        id="phpunit",
    ),
    # ---- Lint gate: any of these should also reset the streak. ----
    # Before this fix, the agent could run `tsc` or `eslint` against its
    # edits and the streak breaker would still nag for vitest specifically.
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm exec eslint ."}}]},
        id="eslint-via-pnpm-exec",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "npx eslint src/"}}]},
        id="eslint-via-npx",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "biome check ."}}]},
        id="biome-check",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "ruff check src/"}}]},
        id="ruff-check",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "golangci-lint run ./..."}}]},
        id="golangci-lint",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "cargo clippy -- -D warnings"}}]},
        id="cargo-clippy",
    ),
    # ---- Types gate ----
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "npx tsc --noEmit"}}]},
        id="tsc-noemit",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm exec tsc"}}]},
        id="tsc-via-pnpm-exec",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "mypy src/"}}]},
        id="mypy",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "cargo check --quiet"}}]},
        id="cargo-check",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "go vet ./..."}}]},
        id="go-vet",
    ),
    # ---- Generic build-system invocations of any gate ----
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "make lint"}}]},
        id="make-lint",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "make typecheck"}}]},
        id="make-typecheck",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "make check"}}]},
        id="make-check",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm run lint"}}]},
        id="pnpm-run-lint",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm run typecheck"}}]},
        id="pnpm-run-typecheck",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "yarn lint"}}]},
        id="yarn-lint-bare",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "bun run verify"}}]},
        id="bun-run-verify",
    ),
    # ---- ucw-verify itself counts ----
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "bin/ucw-verify.py --gates lint,types"}}]},
        id="ucw-verify-direct",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "python bin/ucw-verify.py"}}]},
        id="ucw-verify-via-python",
    ),
]


@pytest.mark.parametrize("payload_extra", POSITIVE_SHAPES)
def test_detector_resets_streak_on_known_test_shape(project, payload_extra):
    payload = {"cwd": str(project), **payload_extra}
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", f"streak below threshold + test detected → silent, got: {out!r}"
    assert _streak(project) == "0", (
        f"shape {payload_extra} should reset streak — _batch_ran_verification "
        f"failed to spot the verify command"
    )


# ---- payload SHAPES that should NOT count as a test run --------------------

NEGATIVE_SHAPES = [
    pytest.param(
        {"tools": []},
        id="empty-batch",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Edit",
                    "tool_input": {"file_path": "/repo/tests/test_pytest.py"}}]},
        id="path-with-pytest-in-filename-must-not-count",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Read",
                    "tool_input": {"file_path": "/repo/jest.config.js"}}]},
        id="path-with-jest-in-filename-must-not-count",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "git log --oneline -5"}}]},
        id="unrelated-bash-call",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "echo 'pytest plugins available'"}}]},
        id="echo-mentioning-pytest-still-counts",
        marks=pytest.mark.xfail(
            reason="word-boundary regex catches bare 'pytest' even inside echo; "
                   "acceptable false-positive — better to over-detect than under-detect"
        ),
    ),
    # ---- Build/dev scripts must NOT count as verification ----
    # Otherwise the streak breaker is useless: an agent running `pnpm dev`
    # every few edits would never get nagged to actually check its work.
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm run dev"}}]},
        id="pnpm-run-dev-does-not-count",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "pnpm run build"}}]},
        id="pnpm-run-build-does-not-count",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "npm start"}}]},
        id="npm-start-does-not-count",
    ),
    pytest.param(
        {"tools": [{"tool_name": "Bash",
                    "tool_input": {"command": "yarn install"}}]},
        id="yarn-install-does-not-count",
    ),
]


@pytest.mark.parametrize("payload_extra", NEGATIVE_SHAPES)
def test_detector_leaves_streak_alone_on_non_test_shapes(project, payload_extra):
    payload = {"cwd": str(project), **payload_extra}
    rc, out, _err = _run(payload)
    assert rc == 0
    # Streak is 3 (below threshold), so no block expected either way
    assert out == ""
    # Streak should remain 3 — these shapes don't represent a test run
    assert _streak(project) == "3", (
        f"shape {payload_extra} should NOT reset streak (no test command)"
    )


# ---- block still fires when no test detected at threshold ------------------

def test_streak_breaker_blocks_when_no_test_detected_and_auto_verify_disabled(project):
    """At threshold (5+), no verify command in batch, and auto-verify
    explicitly disabled → block with the old manual-prompt reason."""
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload, extra_env={"UCW_AUTO_STREAK_VERIFY": "0"})
    assert rc == 0
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "streak breaker" in body["reason"].lower()


def test_streak_breaker_does_not_block_when_test_detected_at_threshold(project):
    """At threshold (5+), a batch with a test command resets and allows."""
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Bash",
                   "tool_input": {"command": "pytest tests/"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", "test run at threshold should silently reset, not block"
    assert _streak(project) == "0"


# ---- PR H: auto-verify on streak break ------------------------------------
# At the threshold, instead of just nagging the agent to run something, the
# hook runs `bin/ucw-verify.py --gates lint,types` itself. Pass → silent
# reset. Fail → block with the gate's failure summary. No runners → reset.

def test_streak_break_auto_verify_passes_resets_silently(project):
    """All fast gates pass → streak resets, no block emitted."""
    (project / "Makefile").write_text(
        "lint:\n\t@true\ntypecheck:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", f"passing auto-verify should be silent, got: {out!r}"
    assert _streak(project) == "0"


def test_streak_break_auto_verify_lint_fail_blocks_with_summary(project):
    """Lint fails at threshold → block names the gate + includes output."""
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'eslint: 3 problems' >&2 && false\n"
        "typecheck:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "lint" in body["reason"].lower()
    assert "eslint: 3 problems" in body["reason"]
    # Streak NOT reset on failure — agent has unfinished work.
    assert _streak(project) == "6"


def test_streak_break_auto_verify_typecheck_fail_blocks_with_summary(project):
    """Lint passes, typecheck fails → block names types gate."""
    (project / "Makefile").write_text(
        "lint:\n\t@true\n"
        "typecheck:\n\t@echo 'TS2322' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("5")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "types" in body["reason"].lower()
    assert "TS2322" in body["reason"]


def test_streak_break_auto_verify_skipped_when_no_runners_resets(project):
    """No Makefile, no package.json, no detectable runners → ucw-verify
    returns skipped. The streak resets — no point blocking when there's
    nothing for the agent to actually run."""
    (project / ".ucw" / "state" / "edit-streak").write_text("8")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", "no runners → quiet reset, not block"
    assert _streak(project) == "0"


def test_streak_break_auto_verify_default_excludes_tests(project):
    """Default UCW_STREAK_GATES is `lint,types` — tests are RESERVED for
    Stop because a full suite is slow. If a project has only a `test:`
    target (no lint/typecheck), verify is skipped and the streak resets."""
    (project / "Makefile").write_text(
        "test:\n\t@echo 'I should not have run' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", f"test gate must NOT run at streak break by default, got: {out!r}"
    assert _streak(project) == "0"


def test_streak_break_env_var_can_include_tests(project):
    """UCW_STREAK_GATES=tests opts into running tests at streak break."""
    (project / "Makefile").write_text(
        "test:\n\t@echo 'test failed' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload, extra_env={"UCW_STREAK_GATES": "tests"})
    assert rc == 0
    body = json.loads(out)
    assert body["decision"] == "block"
    assert "tests" in body["reason"].lower()
    assert "test failed" in body["reason"]


def test_streak_break_below_threshold_does_not_run_verify(project):
    """Below threshold, the hook must NOT shell out to ucw-verify — that
    would add subprocess overhead to every batch. It just returns 0."""
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'I would say I ran' >&2 && false\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("3")  # below 5
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == ""
    # Streak stays at 3 — no edit happened in THIS batch, just a no-op
    assert _streak(project) == "3"


def test_streak_break_logs_gate_outcomes(project):
    """For diagnosability the log line names each gate's pass/FAIL state."""
    (project / "Makefile").write_text(
        "lint:\n\t@true\n"
        "typecheck:\n\t@false\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    _run(payload)
    log = (project / ".ucw" / "hooks.log").read_text()
    assert "auto-verify (streak-break)" in log
    assert "lint=pass" in log
    assert "types=FAIL" in log


# ---- no-progress circuit breaker -------------------------------------------
# An unfixable failure (pre-existing / out-of-scope) would otherwise re-block
# every 5 edits forever. After UCW_VERIFY_BREAK_AFTER identical failures the
# breaker releases the streak so the agent can keep going (Stop is still the
# final gate) and records the stuck failure for the human.

def test_streak_break_circuit_breaker_releases_after_identical_failures(project):
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'E501 line too long' >&2 && false\n"
        "typecheck:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    # Default threshold 3: first two identical failures block, streak preserved.
    for _ in range(2):
        rc, out, _err = _run(payload)
        assert rc == 0
        assert json.loads(out)["decision"] == "block"
        assert _streak(project) == "6"

    # Third identical failure → release: no block, streak reset, stuck recorded.
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", f"breaker should release the streak silently, got: {out!r}"
    assert _streak(project) == "0"
    stuck = project / ".ucw" / "state" / "stuck-verify.md"
    assert stuck.exists()
    assert "E501 line too long" in stuck.read_text()


def test_streak_break_circuit_breaker_resets_on_changed_failure(project):
    """Changing failures = progress; the breaker must not release."""
    mk = project / "Makefile"
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    for msg in ["alpha problem", "beta problem", "alpha problem", "beta problem"]:
        mk.write_text(f"lint:\n\t@echo '{msg}' >&2 && false\ntypecheck:\n\t@true\n")
        _rc, out, _err = _run(payload)
        assert json.loads(out)["decision"] == "block", f"{msg!r} should still block"
    assert not (project / ".ucw" / "state" / "stuck-verify.md").exists()
    assert _streak(project) == "6"


def test_streak_break_circuit_breaker_threshold_env(project):
    """UCW_VERIFY_BREAK_AFTER=1 releases on the first failure."""
    (project / "Makefile").write_text(
        "lint:\n\t@echo 'unfixable' >&2 && false\ntypecheck:\n\t@true\n"
    )
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload, extra_env={"UCW_VERIFY_BREAK_AFTER": "1"})
    assert rc == 0
    assert out == "", "break-after=1 must release on the first failure"
    assert _streak(project) == "0"
    assert (project / ".ucw" / "state" / "stuck-verify.md").exists()


# ---- defensive: malformed payloads must not crash --------------------------

def test_detector_handles_missing_batch_key(project):
    payload = {"cwd": str(project)}  # no tools/batch/etc.
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == ""
    assert _streak(project) == "3"


def test_detector_handles_non_dict_entries(project):
    payload = {"cwd": str(project), "tools": ["not-a-dict", None, 42, {}]}
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == ""


def test_detector_handles_deeply_nested_test_command(project):
    """Even if Claude Code wraps results 3 levels deep, we still find it."""
    payload = {
        "cwd": str(project),
        "envelope": {
            "tool_results": [
                {"meta": {"tool": "Bash"},
                 "payload": {"input": {"command": "pytest -xvs"}}}
            ]
        },
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == ""
    assert _streak(project) == "0", \
        "tree walker must find test commands at any depth"


def test_every_invocation_logs_detection_outcome(project):
    """Every PostToolBatch call must emit a `detection=...` line in hooks.log
    so `tail -f .ucw/hooks.log` shows whether detection fired."""
    payload = {"cwd": str(project), "tools": [
        {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}},
    ]}
    _run(payload)
    log = (project / ".ucw" / "hooks.log").read_text()
    assert "detection=True" in log, (
        f"detection outcome must be logged on every invocation, got:\n{log}"
    )
    assert "streak=3" in log
    assert "batch_keys=" in log


def test_capture_off_by_default(project):
    """Raw payload capture must be opt-in — payloads can contain path data."""
    payload = {"cwd": str(project), "tools": []}
    _run(payload)
    capture = project / ".ucw" / "state" / "post-tool-batch-payloads.jsonl"
    assert not capture.exists(), "payload capture must be off without opt-in"


def test_capture_on_via_sentinel_file(project):
    sentinel = project / ".ucw" / "state" / "debug-payloads"
    sentinel.touch()
    payload = {"cwd": str(project), "tools": [
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    ]}
    _run(payload)
    capture = project / ".ucw" / "state" / "post-tool-batch-payloads.jsonl"
    assert capture.exists(), "sentinel file must enable payload capture"
    line = capture.read_text().strip()
    assert '"ls"' in line, f"captured line should contain the raw payload, got: {line!r}"


def test_capture_on_via_env_var(project, monkeypatch):
    monkeypatch.setenv("UCW_DEBUG_PAYLOADS", "1")
    payload = {"cwd": str(project), "tools": []}
    _run(payload)
    capture = project / ".ucw" / "state" / "post-tool-batch-payloads.jsonl"
    assert capture.exists(), "UCW_DEBUG_PAYLOADS=1 must enable capture"


def test_detector_ignores_test_command_in_path_field(project):
    """Even if a path key contains a test runner name (e.g. file_path =
    `/repo/tests/pytest_runner.py`), we must NOT count it as a test run."""
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/tests/pytest_runner.py"}}],
    }
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == ""
    assert _streak(project) == "3", (
        "file_path key is path-like — must be skipped by the scanner"
    )
