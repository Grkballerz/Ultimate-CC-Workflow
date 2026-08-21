"""Unit tests for bin/ucw-kimi-opinion.py — the Kimi second-opinion review lane.

Both boundaries are ALWAYS mocked here: `kimi_invoke` (so the real
claude-kimi binary is never touched and zero network calls happen) and
`subprocess.run` (so the git-diff and ucw-review.py add-finding shell-outs
are asserted, not run).

The contract under test:
  - happy path: diff collected (--since ref via git, or stdin), ONE review
    prompt built in the reviewers' scope style, each finding Kimi returns
    persisted via `add-finding` with kimi-second-opinion / kimi-k3
    attribution and correct field mapping
  - every failure mode degrades gracefully: warning on stderr, exit 0,
    `{"added": [], "count": 0}` on stdout — the pipeline never breaks.
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_DIFF = """\
diff --git a/src/auth/token.py b/src/auth/token.py
index 1111111..2222222 100644
--- a/src/auth/token.py
+++ b/src/auth/token.py
@@ -40,3 +40,4 @@ def refresh(request):
     body = request.json()
+    subprocess.run(body["cmd"], shell=True)
"""


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_kimi_opinion", REPO_ROOT / "bin" / "ucw-kimi-opinion.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_kimi_opinion"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _completed(stdout: str, returncode: int = 0,
               stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def _finding(**overrides) -> dict:
    """A finding object as Kimi is prompted to emit it."""
    base = {
        "severity": "critical",
        "category": "injection",
        "file": "src/auth/token.py",
        "line": 42,
        "title": "shell exec of request body",
        "detail": "Request body reaches subprocess.run with shell=True.",
        "reproducer": "POST /refresh with cmd='rm -rf /'",
    }
    base.update(overrides)
    return base


def _kimi_ok(data: object) -> dict:
    return {"ok": True, "data": data, "raw": "…", "error": None}


def _kimi_fail(error: str = "claude-kimi timed out after 300s") -> dict:
    return {"ok": False, "data": None, "raw": "", "error": error}


def _added(fid: str = "f-abc123") -> str:
    """add-finding's single-object confirmation line."""
    return json.dumps({"id": fid, "effective_severity": "critical"}) + "\n"


def _payload_of(call) -> dict:
    """Extract the --json payload from an add-finding shell-out."""
    cmd = call.args[0]
    return json.loads(cmd[cmd.index("--json") + 1])


# ---- happy path: findings persisted with kimi attribution -------------------

def test_happy_path_submits_each_finding_with_kimi_attribution(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),   # git diff main..HEAD
        _completed(_added("f-1")),  # add-finding #1
        _completed(_added("f-2")),  # add-finding #2
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok([
        _finding(),
        _finding(severity="minor", category="tests", line=7,
                 title="no regression test", reproducer=None),
    ]))
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    assert run.call_count == 3
    # first shell-out collects the diff
    git_cmd = run.call_args_list[0].args[0]
    assert git_cmd[:2] == ["git", "diff"]
    assert "main..HEAD" in git_cmd
    # each finding goes through ucw-review.py add-finding
    for call in run.call_args_list[1:]:
        cmd = call.args[0]
        assert "add-finding" in cmd
        assert cmd[cmd.index("--repo") + 1] == "/srv/app"
        payload = _payload_of(call)
        assert payload["finder_agent"] == "kimi-second-opinion"
        assert payload["finder_model"] == "kimi-k3"
    # field mapping survives the round-trip
    first = _payload_of(run.call_args_list[1])
    assert first["severity"] == "critical"
    assert first["category"] == "injection"
    assert first["file"] == "src/auth/token.py"
    assert first["line"] == 42
    assert first["title"] == "shell exec of request body"
    assert "shell=True" in first["detail"]
    assert first["reproducer"].startswith("POST /refresh")
    second = _payload_of(run.call_args_list[2])
    assert second["severity"] == "minor"
    assert second["category"] == "tests"
    assert "reproducer" not in second  # omitted when Kimi has none
    # the ids come back on stdout
    assert json.loads(capsys.readouterr().out) == {
        "added": ["f-1", "f-2"], "count": 2,
    }


def test_stdin_diff_is_reviewed_without_git(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(_added("f-9")))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok([_finding()]))
    monkeypatch.setattr(mod, "kimi_invoke", kimi)
    monkeypatch.setattr(sys, "stdin", io.StringIO(SAMPLE_DIFF))

    rc = mod.main(["--repo", "/srv/app"])

    assert rc == 0
    # no --since → no git shell-out; the only run() call is add-finding
    assert run.call_count == 1
    assert "add-finding" in run.call_args.args[0]
    assert SAMPLE_DIFF.strip() in kimi.call_args.args[0]
    assert json.loads(capsys.readouterr().out)["added"] == ["f-9"]


def test_tty_stdin_without_since_warns_and_exits_zero(monkeypatch, capsys):
    """No --since + interactive terminal must never block on stdin.read()."""
    mod = _load()
    run = mock.Mock()
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)
    tty_stdin = mock.Mock()
    tty_stdin.isatty.return_value = True
    monkeypatch.setattr(sys, "stdin", tty_stdin)

    rc = mod.main(["--repo", "/srv/app"])

    assert rc == 0
    tty_stdin.read.assert_not_called()  # the never-hang property
    assert run.call_count == 0
    assert kimi.call_count == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "warning" in err
    assert "--since" in err  # the warning tells the operator the way out


def test_sha_flag_is_forwarded_to_add_finding(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),
        _completed(_added()),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke",
                        mock.Mock(return_value=_kimi_ok([_finding()])))

    rc = mod.main(["--repo", "/srv/app", "--since", "main", "--sha", "cafef00d"])

    assert rc == 0
    cmd = run.call_args_list[1].args[0]
    assert cmd[cmd.index("--sha") + 1] == "cafef00d"
    capsys.readouterr()


def test_prompt_contains_reviewer_scopes_diff_and_json_contract(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(SAMPLE_DIFF))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok([]))
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    mod.main(["--repo", "/srv/app", "--since", "main"])

    prompt = kimi.call_args.args[0]
    # every reviewer concern is offered as a category…
    for concern in mod.CATEGORIES:
        assert f"- {concern}" in prompt
    # …and the category names show up regardless of frontmatter wording
    # (don't couple to any one reviewer file's description text)
    assert sum(concern in prompt for concern in mod.CATEGORIES) >= 3
    # the severity rubric and the JSON-array contract are stated
    assert "critical" in prompt and "nit" in prompt
    assert "JSON array" in prompt
    # the diff under review is embedded
    assert 'subprocess.run(body["cmd"], shell=True)' in prompt
    capsys.readouterr()


def test_wrapped_findings_object_is_unwrapped(monkeypatch, capsys):
    """Kimi wrapping the array in {"findings": [...]} still persists."""
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),
        _completed(_added("f-5")),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke",
                        mock.Mock(return_value=_kimi_ok({"findings": [_finding()]})))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["added"] == ["f-5"]


def test_empty_array_is_a_clean_zero_finding_run(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(SAMPLE_DIFF))
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok([])))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    assert run.call_count == 1  # git diff only — nothing to persist
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "unusable" not in err  # an empty array is legitimate, not a failure


def test_max_findings_caps_persistence(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),
        _completed(_added("f-1")),
        _completed(_added("f-2")),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok(
        [_finding(line=n) for n in range(1, 6)]
    )))

    rc = mod.main(["--repo", "/srv/app", "--since", "main", "--max-findings", "2"])

    assert rc == 0
    assert run.call_count == 3  # git diff + exactly 2 add-finding calls
    out, err = capsys.readouterr()
    assert json.loads(out)["count"] == 2
    assert "warning" in err


# ---- coercion of imperfect finding objects ----------------------------------

def test_invalid_enum_values_are_coerced_not_dropped(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),
        _completed(_added()),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok([
        _finding(severity="catastrophic", category="vibes", line="not-a-number"),
    ])))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    payload = _payload_of(run.call_args_list[1])
    # invalid enums degrade to safe defaults instead of crashing add-finding
    assert payload["severity"] == mod.DEFAULT_SEVERITY
    assert payload["category"] == mod.DEFAULT_CATEGORY
    assert payload["line"] == 0
    assert "warning" in capsys.readouterr().err


def test_non_object_entries_are_skipped_with_warning(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),
        _completed(_added("f-1")),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok(
        ["looks fine to me", 42, _finding()]
    )))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    assert run.call_count == 2  # only the one real finding is persisted
    out, err = capsys.readouterr()
    assert json.loads(out)["count"] == 1
    assert "skipping" in err


# ---- failure degrades gracefully: warn, exit 0, zero findings ---------------

def test_kimi_failure_warns_and_persists_nothing(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(SAMPLE_DIFF))
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_fail()))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0  # advisory lane: pipeline must not break
    assert run.call_count == 1  # git diff only — add-finding never called
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "warning" in err
    assert "timed out" in err


def test_unusable_kimi_payload_warns_and_persists_nothing(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(SAMPLE_DIFF))
    monkeypatch.setattr(mod.subprocess, "run", run)
    # parsable JSON, but structurally not findings in any recognizable shape
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok(
        {"verdict": "looks good", "confidence": 0.9}
    )))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    assert run.call_count == 1  # no add-finding shell-out
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "unusable" in err


def test_git_diff_failure_warns_and_never_calls_kimi(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed("", returncode=128,
                                            stderr="fatal: bad revision"))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["--repo", "/srv/app", "--since", "nope"])

    assert rc == 0
    assert kimi.call_count == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "warning" in err


def test_empty_diff_warns_and_never_calls_kimi(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(""))  # clean git exit, no changes
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    assert kimi.call_count == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "empty diff" in err


def test_add_finding_failure_warns_and_exits_zero(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(SAMPLE_DIFF),
        _completed("", returncode=2, stderr="invalid severity"),  # 1st rejected
        _completed(_added("f-2")),                                # 2nd lands
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok(
        [_finding(), _finding(line=7)]
    )))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    out, err = capsys.readouterr()
    # the surviving finding is still reported; the broken one is warned about
    assert json.loads(out) == {"added": ["f-2"], "count": 1}
    assert "add-finding exited 2" in err


def test_unexpected_exception_is_swallowed_with_warning(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(SAMPLE_DIFF))
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke",
                        mock.Mock(side_effect=RuntimeError("boom")))

    rc = mod.main(["--repo", "/srv/app", "--since", "main"])

    assert rc == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == {"added": [], "count": 0}
    assert "unexpected error" in err


# ---- _coerce_findings unit coverage -----------------------------------------

def test_coerce_findings_rejects_non_finding_shapes():
    mod = _load()
    assert mod._coerce_findings("prose review") is None
    assert mod._coerce_findings(42) is None
    assert mod._coerce_findings(None) is None
    assert mod._coerce_findings({"summary": "all good"}) is None
    # a lone 'file' key is not enough to count as a bare finding
    assert mod._coerce_findings({"file": "x"}) is None


def test_coerce_findings_accepts_single_bare_finding_object():
    mod = _load()

    out = mod._coerce_findings(_finding())

    assert out is not None and len(out) == 1
    assert out[0]["finder_agent"] == "kimi-second-opinion"
    assert out[0]["finder_model"] == "kimi-k3"


def test_coerce_findings_accepts_title_plus_file_without_severity(capsys):
    """'title'+'file' is a recognizable finding even with no severity key."""
    mod = _load()

    out = mod._coerce_findings({"title": "unbounded query",
                                "file": "src/db/orders.py"})

    assert out is not None and len(out) == 1
    assert out[0]["title"] == "unbounded query"
    assert out[0]["file"] == "src/db/orders.py"
    # the missing severity is coerced to the safe default, with a warning
    assert out[0]["severity"] == mod.DEFAULT_SEVERITY
    assert "warning" in capsys.readouterr().err


def test_coerce_findings_skips_non_conforming_dict_entries(capsys):
    """List entries lacking 'severity' or 'title'+'file' are skipped —
    never padded into an empty minor/correctness finding."""
    mod = _load()

    out = mod._coerce_findings([
        {"file": "src/auth/token.py"},          # lone file key — skipped
        {"note": "looks fine", "line": 3},       # no finding keys — skipped
        _finding(),                              # a real one — kept
    ])

    assert out is not None and len(out) == 1
    assert out[0]["title"] == "shell exec of request body"
    err = capsys.readouterr().err
    assert err.count("skipping non-conforming") == 2


def test_coerce_findings_empty_list_is_not_an_error():
    mod = _load()
    assert mod._coerce_findings([]) == []


# ---- prompt construction unit coverage --------------------------------------

def test_build_prompt_truncates_oversized_diffs():
    mod = _load()

    prompt = mod.build_prompt("x" * (mod.MAX_DIFF_CHARS + 5000))

    assert "[diff truncated]" in prompt
    assert len(prompt) < mod.MAX_DIFF_CHARS + 5000
