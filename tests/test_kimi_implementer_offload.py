"""Doc, constant, and runtime contract for [kimi] task offload.

Doc + constant invariants:

- agents/implementer.md documents the [kimi] tag, the kimi.offload
  kill-switch gate, and the mandatory self-verification of Kimi diffs.
- Nothing in implementer.md instructs skipping the verifier/reviewer for
  Kimi-authored diffs — same bar as Claude-authored ones.
- bin/ucw-kimi-implement.py's tool allowlist excludes Bash (the offloaded
  implementer edits files; it never gets a shell). The constant is
  imported, not regexed, so a rename or edit can't dodge the check.

Runtime invariants (subprocess.run is ALWAYS mocked — zero network, no
real claude-kimi, mirroring tests/test_kimi_disprove.py):

- every invocation failure (empty prompt, missing repo, timeout, missing
  binary, launch error, nonzero exit) is exit code 1 with a stderr message
- a broken git-status summary degrades the output, never the exit code
- NO constructed command ever mutates git — the only git use is the
  read-only `git status --porcelain` inspection
- the kimi timeout is settings-resolved (env > settings.json > 600) with
  --timeout as explicit override
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTER_MD = REPO_ROOT / "agents" / "implementer.md"


def _load_kimi_implement():
    spec = importlib.util.spec_from_file_location(
        "ucw_kimi_implement", REPO_ROOT / "bin" / "ucw-kimi-implement.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_kimi_implement"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _implementer_text() -> str:
    return IMPLEMENTER_MD.read_text(encoding="utf-8")


# ---- the [kimi] routing rule is documented -----------------------------------

def test_implementer_documents_kimi_tag():
    text = _implementer_text()
    assert "[kimi]" in text, \
        "agents/implementer.md must document the [kimi] plan-task tag"


def test_implementer_documents_offload_gate():
    """The tag is honored only behind the kimi.offload kill-switch."""
    text = _implementer_text()
    assert "kimi.offload" in text, \
        "agents/implementer.md must gate [kimi] on the kimi.offload setting"
    assert "ucw-settings" in text, \
        "the gate must be read via ucw-settings, not assumed"


def test_implementer_documents_offload_default_off():
    text = _implementer_text().lower()
    assert "default: false" in text or "default false" in text or \
           "default: off" in text, \
        "implementer.md must state that kimi.offload defaults to off"


def test_implementer_routes_honored_tasks_to_kimi_implement():
    text = _implementer_text()
    assert "ucw-kimi-implement.py" in text


def test_implementer_mandates_verification_of_kimi_diffs():
    """The Kimi diff is unverified until the normal gates pass."""
    text = _implementer_text()
    assert re.search(r"MUST run (its|your) own verification", text), \
        "implementer.md must mandate self-verification after a Kimi offload"
    assert "unverified" in text.lower()


# ---- no instruction to skip gates for kimi tasks -----------------------------
# "skip verification" may appear only in negated form ("Don't skip
# verification steps" is the pre-existing hard rule).

_NEGATIONS = ("don't", "do not", "never", "must not", "not ")


def test_implementer_never_instructs_skipping_gates_for_kimi():
    for i, line in enumerate(_implementer_text().splitlines(), start=1):
        low = line.lower()
        if not re.search(r"skip\w*\s+(the\s+)?(verif|review)", low):
            continue
        assert any(n in low for n in _NEGATIONS), (
            f"implementer.md:{i} instructs skipping verification/review — "
            f"Kimi-authored diffs get the identical bar to Claude-authored "
            f"ones. Line: {line.strip()!r}"
        )


# ---- the offloaded implementer never gets a shell ----------------------------

def test_kimi_implement_allowlist_excludes_bash():
    mod = _load_kimi_implement()
    tools = [t.strip() for t in mod.KIMI_ALLOWED_TOOLS.split(",")]
    assert "Bash" not in tools, \
        "KIMI_ALLOWED_TOOLS must exclude Bash — the offloaded implementer " \
        "edits files, it never runs shell commands"


def test_kimi_implement_allowlist_grants_file_edit_tools():
    mod = _load_kimi_implement()
    tools = {t.strip() for t in mod.KIMI_ALLOWED_TOOLS.split(",")}
    assert {"Read", "Edit", "Write"} <= tools


def test_kimi_implement_build_cmd_uses_the_allowlist(tmp_path):
    """build_cmd forwards the constant verbatim — no drift between the
    documented contract and the actual claude-kimi invocation."""
    mod = _load_kimi_implement()
    cmd = mod.build_cmd("do the thing", tmp_path)
    assert cmd[0] == "claude-kimi"
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == mod.KIMI_ALLOWED_TOOLS


# ---- runtime: mocked-subprocess coverage of main() ---------------------------

def _completed(stdout: str, returncode: int = 0,
               stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude-kimi"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


def _no_env_timeout(monkeypatch):
    monkeypatch.delenv("UCW_KIMI_TIMEOUT_SECS", raising=False)


def test_happy_path_runs_kimi_then_prints_changed_files(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=[
        _completed("Refactored the parser as asked."),   # claude-kimi
        _completed(" M src/parser.py\n?? src/new.py\n"),  # git status
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["refactor the parser", "--repo", str(tmp_path)])

    assert rc == 0
    assert run.call_count == 2
    kimi_cmd = run.call_args_list[0].args[0]
    assert kimi_cmd[0] == "claude-kimi"
    assert kimi_cmd[kimi_cmd.index("--allowedTools") + 1] == mod.KIMI_ALLOWED_TOOLS
    assert kimi_cmd[kimi_cmd.index("--add-dir") + 1] == str(tmp_path)
    out = capsys.readouterr().out
    assert "Refactored the parser" in out
    assert " M src/parser.py" in out
    assert "?? src/new.py" in out


def test_dash_prompt_is_read_from_stdin(monkeypatch, tmp_path):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=[_completed("ok"), _completed("")])
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("add retry logic\n"))

    rc = mod.main(["-", "--repo", str(tmp_path)])

    assert rc == 0
    kimi_cmd = run.call_args_list[0].args[0]
    assert kimi_cmd[kimi_cmd.index("-p") + 1] == "add retry logic"


def test_empty_prompt_fails_without_spawning_anything(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    run = mock.Mock()
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["   ", "--repo", str(tmp_path)])

    assert rc == 1
    assert run.call_count == 0
    assert "empty task prompt" in capsys.readouterr().err


def test_missing_repo_dir_fails_without_spawning_anything(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    run = mock.Mock()
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path / "gone")])

    assert rc == 1
    assert run.call_count == 0
    assert "repo dir not found" in capsys.readouterr().err


def test_kimi_timeout_is_exit_one_with_message(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=subprocess.TimeoutExpired(
        cmd=["claude-kimi"], timeout=600))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 1
    assert "timed out after 600s" in capsys.readouterr().err


def test_missing_binary_is_exit_one_with_message(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=FileNotFoundError("claude-kimi"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 1
    assert "claude-kimi not found on PATH" in capsys.readouterr().err


def test_launch_oserror_is_exit_one_with_message(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=OSError("argument list too long"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 1
    assert "failed to launch claude-kimi" in capsys.readouterr().err


def test_nonzero_exit_is_exit_one_with_stderr_detail(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(return_value=_completed(
        "", returncode=2, stderr="auth expired"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "exited 2" in err
    assert "auth expired" in err


def test_git_status_failure_degrades_summary_not_exit_code(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=[
        _completed("done"),                                    # claude-kimi ok
        _completed("", returncode=128, stderr="not a git repository"),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 0  # the kimi run itself succeeded
    out = capsys.readouterr().out
    assert "summary unavailable" in out
    assert "not a git repository" in out


def test_git_status_hang_is_bounded_and_degrades_summary(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=[
        _completed("done"),
        subprocess.TimeoutExpired(cmd=["git", "status"], timeout=60),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 0
    assert "summary unavailable" in capsys.readouterr().out
    # the status shell-out itself must carry the 60s bound
    assert run.call_args_list[1].kwargs["timeout"] == 60


def test_no_edits_summary_when_git_status_is_clean(monkeypatch, tmp_path, capsys):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=[_completed("nothing to do"), _completed("")])
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["do things", "--repo", str(tmp_path)])

    assert rc == 0
    assert "Kimi made no edits" in capsys.readouterr().out


# ---- HARD invariant: no constructed command ever mutates git -----------------

def test_no_constructed_command_ever_mutates_git(monkeypatch, tmp_path, capsys):
    """Capture EVERY subprocess call of a full successful run: none may
    contain `git add`, `git commit`, or `git push`, and the only git use is
    the read-only status inspection."""
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    calls = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "git":
            return _completed(" M src/app.py\n")
        return _completed("edited src/app.py")

    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["fix the bug", "--repo", str(tmp_path)])

    assert rc == 0
    assert calls, "the invariant is vacuous if nothing ran"
    for cmd in calls:
        joined = " ".join(str(part) for part in cmd)
        assert "git add" not in joined
        assert "git commit" not in joined
        assert "git push" not in joined
    git_cmds = [c for c in calls if c[0] == "git"]
    assert git_cmds == [["git", "status", "--porcelain"]]


# ---- timeout resolution: env > settings.json > default, flag overrides ------

def test_timeout_defaults_to_600_when_nothing_is_configured(monkeypatch, tmp_path):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    run = mock.Mock(side_effect=[_completed("ok"), _completed("")])
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.main(["do things", "--repo", str(tmp_path)])

    assert run.call_args_list[0].kwargs["timeout"] == 600


def test_timeout_env_var_wins(monkeypatch, tmp_path):
    mod = _load_kimi_implement()
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "45")
    run = mock.Mock(side_effect=[_completed("ok"), _completed("")])
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.main(["do things", "--repo", str(tmp_path)])

    assert run.call_args_list[0].kwargs["timeout"] == 45


def test_timeout_read_from_project_settings_json(monkeypatch, tmp_path):
    mod = _load_kimi_implement()
    _no_env_timeout(monkeypatch)
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "settings.json").write_text(
        json.dumps({"kimi.timeout_secs": 900}), encoding="utf-8")
    run = mock.Mock(side_effect=[_completed("ok"), _completed("")])
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.main(["do things", "--repo", str(tmp_path)])

    assert run.call_args_list[0].kwargs["timeout"] == 900


def test_timeout_flag_overrides_env_and_settings(monkeypatch, tmp_path):
    mod = _load_kimi_implement()
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "45")
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "settings.json").write_text(
        json.dumps({"kimi.timeout_secs": 900}), encoding="utf-8")
    run = mock.Mock(side_effect=[_completed("ok"), _completed("")])
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.main(["do things", "--repo", str(tmp_path), "--timeout", "30"])

    assert run.call_args_list[0].kwargs["timeout"] == 30


def test_resolve_timeout_ignores_invalid_env_and_settings_values(monkeypatch, tmp_path):
    """Same escape-hatch tolerance as ucw-settings: garbage falls through."""
    mod = _load_kimi_implement()
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "soon")
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    (state / "settings.json").write_text(
        json.dumps({"kimi.timeout_secs": "fast"}), encoding="utf-8")

    assert mod.resolve_timeout(tmp_path) == mod.DEFAULT_TIMEOUT
