"""Unit tests for bin/ucw-kimi-disprove.py — the Kimi cross-audit disprover.

Both boundaries are ALWAYS mocked here: `kimi_invoke` (so the real
claude-kimi binary is never touched and zero network calls happen) and
`subprocess.run` (so the ucw-review.py shell-outs are asserted, not run).

The contract under test:
  - happy path: finding looked up via `list --json` (or handed over directly
    via --finding-json, skipping the list scan), disprover prompt built
    from agents/disprover.md (frontmatter stripped), kimi's verdict recorded
    via `disprove <id> <verdict>` with kimi-disprover / kimi-k3 attribution
  - the kimi toolset is read-only investigation — never Bash
  - every failure mode degrades gracefully: warning on stderr, exit 0, and
    the disprove CLI is NEVER called — a missing verdict stays missing.
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


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_kimi_disprove", REPO_ROOT / "bin" / "ucw-kimi-disprove.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_kimi_disprove"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _completed(stdout: str, returncode: int = 0,
               stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ucw-review.py"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


def _row(fid: str = "f-abc123") -> dict:
    """A finding row as `ucw-review.py list --json` emits it."""
    return {
        "id": fid, "sha": "deadbeef" * 5, "severity": "critical",
        "category": "injection", "file": "src/auth/token.py", "line": 42,
        "title": "shell exec of request body", "detail": "body reaches subprocess",
        "reproducer": None, "finder_agent": "reviewer-injection",
        "finder_model": "sonnet", "effective_severity": "critical",
        "approved": False,
    }


def _kimi_ok(verdict: str = "confirmed",
             evidence: str = "src/auth/token.py:42 — reachable from POST") -> dict:
    return {"ok": True, "data": {"verdict": verdict, "evidence": evidence},
            "raw": "…", "error": None}


def _kimi_fail(error: str = "claude-kimi timed out after 300s") -> dict:
    return {"ok": False, "data": None, "raw": "", "error": error}


# ---- happy path: verdict parsed and recorded --------------------------------

def test_happy_path_records_verdict_with_kimi_attribution(monkeypatch, capsys):
    mod = _load()
    disprove_out = json.dumps({"id": "f-abc123", "verdict": "confirmed",
                               "effective_severity": "critical"}) + "\n"
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),  # list --json
        _completed(disprove_out),          # disprove
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok("confirmed"))
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    assert run.call_count == 2
    # first shell-out is the read path: ucw-review.py list --json
    list_cmd = run.call_args_list[0].args[0]
    assert "list" in list_cmd and "--json" in list_cmd
    # second shell-out records the verdict with the exact disprove signature
    cmd = run.call_args_list[1].args[0]
    assert "disprove" in cmd
    d = cmd.index("disprove")
    assert cmd[cmd.index("f-abc123", d) + 1] == "confirmed"  # positional id verdict
    assert cmd[cmd.index("--agent") + 1] == "kimi-disprover"
    assert cmd[cmd.index("--model") + 1] == "kimi-k3"
    assert "token.py:42" in cmd[cmd.index("--evidence") + 1]
    # the disprove CLI's confirmation is forwarded to stdout
    assert json.loads(capsys.readouterr().out)["verdict"] == "confirmed"


def test_prompt_contains_disprover_body_and_finding_but_not_frontmatter(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok("refuted"))
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    mod.main(["f-abc123", "--repo", "/srv/app"])

    prompt = kimi.call_args.args[0]
    # instructions from agents/disprover.md survive…
    assert "try to disprove" in prompt.lower()
    # …the YAML frontmatter (incl. the protected `model: haiku` line) does not
    assert "model: haiku" not in prompt
    # the finding under audit is embedded
    assert "f-abc123" in prompt
    assert "src/auth/token.py" in prompt
    # headless override: kimi must not shell out to the CLI itself
    assert "do NOT invoke ucw-review.py" in prompt


def test_refuted_verdict_is_passed_through(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke",
                        mock.Mock(return_value=_kimi_ok("refuted", "constant input")))

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    cmd = run.call_args_list[1].args[0]
    assert "refuted" in cmd


def test_verdict_normalized_from_case_and_underscores(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke",
                        mock.Mock(return_value=_kimi_ok("Needs_Human", "ask the user")))

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    cmd = run.call_args_list[1].args[0]
    assert "needs-human" in cmd


def test_sha_flag_is_forwarded_to_disprove(monkeypatch):
    mod = _load()
    sha = _row()["sha"]
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok()))

    rc = mod.main(["f-abc123", "--repo", "/srv/app", "--sha", sha[:8]])

    assert rc == 0
    cmd = run.call_args_list[1].args[0]
    assert cmd[cmd.index("--sha") + 1] == sha[:8]


# ---- the disprover never gets a shell ----------------------------------------

def test_allowed_tools_exclude_bash():
    """Finding text is attacker-influenceable (it quotes reviewed code) —
    a cross-vendor model with pre-approved Bash would be a prompt-injection-
    to-exec path. The disprover reads code; it never runs it."""
    mod = _load()
    tools = [t.strip() for t in mod.ALLOWED_TOOLS.split(",")]
    assert "Bash" not in tools
    assert {"Read", "Grep", "Glob"} == set(tools)


def test_allowed_tools_are_forwarded_to_kimi_invoke(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok())
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    mod.main(["f-abc123", "--repo", "/srv/app"])

    assert kimi.call_args.kwargs["allowed_tools"] == mod.ALLOWED_TOOLS


# ---- timeouts: shell-outs bounded, kimi timeout settings-resolved ------------

def test_review_cli_shell_outs_carry_a_timeout(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok()))

    mod.main(["f-abc123", "--repo", "/srv/app"])

    for call in run.call_args_list:  # list --json AND disprove
        assert call.kwargs["timeout"] == 60


def test_kimi_timeout_defaults_to_none_for_settings_resolution(monkeypatch):
    """No --timeout flag → timeout=None reaches kimi_invoke, which resolves
    UCW_KIMI_TIMEOUT_SECS / kimi.timeout_secs / 300 itself."""
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok())
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    mod.main(["f-abc123", "--repo", "/srv/app"])

    assert kimi.call_args.kwargs["timeout"] is None


def test_explicit_timeout_flag_overrides_settings_resolution(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("{}"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok())
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    mod.main(["f-abc123", "--repo", "/srv/app", "--timeout", "120"])

    assert kimi.call_args.kwargs["timeout"] == 120


# ---- --finding-json: pipeline hands the finding over directly ----------------

def test_finding_json_arg_skips_the_list_scan(monkeypatch, capsys):
    mod = _load()
    disprove_out = json.dumps({"id": "f-abc123", "verdict": "confirmed"}) + "\n"
    run = mock.Mock(return_value=_completed(disprove_out))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok("confirmed"))
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-abc123", "--repo", "/srv/app",
                   "--finding-json", json.dumps(_row())])

    assert rc == 0
    # exactly ONE shell-out — the disprove record; no `list --json` scan
    assert run.call_count == 1
    cmd = run.call_args.args[0]
    assert "disprove" in cmd
    assert "list" not in cmd
    # the finding still lands in the prompt
    prompt = kimi.call_args.args[0]
    assert "src/auth/token.py" in prompt
    assert json.loads(capsys.readouterr().out)["verdict"] == "confirmed"


def test_finding_json_dash_reads_finding_from_stdin(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock(return_value=_kimi_ok())
    monkeypatch.setattr(mod, "kimi_invoke", kimi)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(json.dumps(_row())))

    rc = mod.main(["f-abc123", "--repo", "/srv/app", "--finding-json", "-"])

    assert rc == 0
    assert run.call_count == 1  # disprove only
    assert "src/auth/token.py" in kimi.call_args.args[0]


def test_unparsable_finding_json_warns_and_records_nothing(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock()
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-abc123", "--repo", "/srv/app",
                   "--finding-json", "not json {"])

    assert rc == 0
    assert kimi.call_count == 0
    assert run.call_count == 0  # neither list nor disprove
    assert "no verdict recorded" in capsys.readouterr().err


def test_finding_json_non_object_payload_is_rejected(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock()
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-abc123", "--repo", "/srv/app",
                   "--finding-json", json.dumps([_row()])])  # array, not object

    assert rc == 0
    assert kimi.call_count == 0
    assert "no verdict recorded" in capsys.readouterr().err


def test_finding_json_id_mismatch_warns_and_records_nothing(monkeypatch, capsys):
    """A verdict computed on one finding must never be recorded under
    another id — mismatch degrades exactly like an unknown finding."""
    mod = _load()
    run = mock.Mock()
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-other99", "--repo", "/srv/app",
                   "--finding-json", json.dumps(_row("f-abc123"))])

    assert rc == 0
    assert kimi.call_count == 0
    assert run.call_count == 0
    err = capsys.readouterr().err
    assert "does not match" in err
    assert "no verdict recorded" in err


# ---- parse_verdict unit coverage --------------------------------------------

def test_parse_verdict_accepts_all_known_verdicts():
    mod = _load()
    for v in ("confirmed", "refuted", "needs-human"):
        assert mod.parse_verdict({"verdict": v, "evidence": "e"}) == (v, "e")


def test_parse_verdict_rejects_unknown_verdict_and_non_dict():
    mod = _load()
    assert mod.parse_verdict({"verdict": "maybe", "evidence": "e"}) is None
    assert mod.parse_verdict(["confirmed"]) is None
    assert mod.parse_verdict(None) is None
    assert mod.parse_verdict({"evidence": "no verdict key"}) is None


def test_parse_verdict_tolerates_missing_evidence():
    mod = _load()
    assert mod.parse_verdict({"verdict": "confirmed"}) == ("confirmed", "")


# ---- failure degrades gracefully: warn, exit 0, NEVER record ----------------

def test_kimi_failure_warns_and_records_nothing(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(json.dumps([_row()])))
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_fail()))

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0  # advisory step: pipeline must not break
    assert run.call_count == 1  # only the list read — disprove never called
    err = capsys.readouterr().err
    assert "warning" in err
    assert "no verdict recorded" in err


def test_unparsable_kimi_verdict_warns_and_records_nothing(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(json.dumps([_row()])))
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value={
        "ok": True, "data": {"verdict": "probably fine??"}, "raw": "…", "error": None,
    }))

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    assert run.call_count == 1  # no disprove shell-out
    assert "no verdict recorded" in capsys.readouterr().err


def test_unknown_finding_warns_and_never_calls_kimi(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed("[]"))  # list has no such id
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-nope", "--repo", "/srv/app"])

    assert rc == 0
    assert kimi.call_count == 0
    assert run.call_count == 1
    assert "f-nope" in capsys.readouterr().err


def test_list_cli_failure_warns_and_exits_zero(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed("", returncode=2, stderr="boom"))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    assert kimi.call_count == 0
    assert "no verdict recorded" in capsys.readouterr().err


def test_disprove_cli_failure_warns_and_exits_zero(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed(json.dumps([_row()])),
        _completed("", returncode=2, stderr="unknown finding"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "kimi_invoke", mock.Mock(return_value=_kimi_ok()))

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    assert "disprove failed" in capsys.readouterr().err


def test_prompt_file_missing_warns_and_exits_zero(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed(json.dumps([_row()])))
    monkeypatch.setattr(mod.subprocess, "run", run)
    kimi = mock.Mock()
    monkeypatch.setattr(mod, "kimi_invoke", kimi)
    # main() calls load_prompt_body() with its baked-in default path — patch
    # the function itself to simulate the missing file
    monkeypatch.setattr(mod, "load_prompt_body",
                        lambda path=None: None)

    rc = mod.main(["f-abc123", "--repo", "/srv/app"])

    assert rc == 0
    assert kimi.call_count == 0
    assert "no verdict recorded" in capsys.readouterr().err


# ---- load_prompt_body unit coverage -----------------------------------------

def test_load_prompt_body_strips_frontmatter():
    mod = _load()

    body = mod.load_prompt_body()

    assert body is not None
    assert "model: haiku" not in body
    assert "disprove" in body.lower()


def test_load_prompt_body_returns_none_on_missing_file(tmp_path):
    mod = _load()

    assert mod.load_prompt_body(tmp_path / "nope.md") is None
