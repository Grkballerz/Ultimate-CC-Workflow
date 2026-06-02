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


def _run(payload: dict) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    cp = subprocess.run(
        [sys.executable, str(HOOKS / "post-tool-batch.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=10,
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
]


@pytest.mark.parametrize("payload_extra", POSITIVE_SHAPES)
def test_detector_resets_streak_on_known_test_shape(project, payload_extra):
    payload = {"cwd": str(project), **payload_extra}
    rc, out, _err = _run(payload)
    assert rc == 0
    assert out == "", f"streak below threshold + test detected → silent, got: {out!r}"
    assert _streak(project) == "0", (
        f"shape {payload_extra} should reset streak — _batch_ran_tests "
        f"failed to spot the test command"
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

def test_streak_breaker_still_blocks_when_no_test_detected(project):
    """At threshold (5+), a batch without a test command must block."""
    (project / ".ucw" / "state" / "edit-streak").write_text("7")
    payload = {
        "cwd": str(project),
        "tools": [{"tool_name": "Edit",
                   "tool_input": {"file_path": "/repo/src/app.py"}}],
    }
    rc, out, _err = _run(payload)
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
