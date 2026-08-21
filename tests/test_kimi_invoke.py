"""Unit tests for bin/kimi_invoke.py — the claude-kimi bridge plumbing.

subprocess.run is ALWAYS mocked here: these tests make zero network calls
and never invoke the real claude-kimi binary. What we verify is the
plumbing around it — command construction, timeout/model resolution
(env > project settings > default), raw mode, lenient JSON extraction
(last block wins), retry-on-malformed, and the never-raise failure contract.
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
        "kimi_invoke", REPO_ROOT / "bin" / "kimi_invoke.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["kimi_invoke"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _completed(stdout: str, returncode: int = 0,
               stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude-kimi", "-p", "x"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


def _make_project(tmp_path: Path, settings: dict | None = None) -> Path:
    """Turn *tmp_path* into a UCW project root the upward walk stops at.

    A `.ucw` dir always exists (so resolution never escapes into the real
    repo); *settings*, when given, lands in `.ucw/state/settings.json`.
    """
    state = tmp_path / ".ucw" / "state"
    state.mkdir(parents=True)
    if settings is not None:
        (state / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8")
    return tmp_path


def _isolate_resolution_env(monkeypatch) -> None:
    monkeypatch.delenv("UCW_KIMI_TIMEOUT_SECS", raising=False)
    monkeypatch.delenv("UCW_KIMI_MODEL", raising=False)


# ---- happy path --------------------------------------------------------------

def test_invoke_returns_parsed_data_when_output_is_clean_json(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed('{"verdict": "pass", "score": 5}'))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("review src/auth/token.py for injection")

    assert result["ok"] is True
    assert result["data"] == {"verdict": "pass", "score": 5}
    assert result["error"] is None
    assert '"verdict"' in result["raw"]
    assert run.call_count == 1


def test_invoke_passes_prompt_and_timeout_to_subprocess(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("summarize the diff", timeout=42)

    cmd = run.call_args.args[0]
    assert cmd[:3] == ["claude-kimi", "-p", "summarize the diff"]
    assert run.call_args.kwargs["timeout"] == 42


def test_invoke_appends_allowed_tools_and_add_dir_flags(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke(
        "audit the repo",
        allowed_tools=["Read", "Grep"],
        add_dirs=["/srv/app", "/srv/lib"],
    )

    cmd = run.call_args.args[0]
    i = cmd.index("--allowedTools")
    assert cmd[i + 1] == "Read,Grep"
    assert cmd.count("--add-dir") == 2
    assert "/srv/app" in cmd and "/srv/lib" in cmd


def test_invoke_omits_flags_when_args_not_given(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("plain call")

    cmd = run.call_args.args[0]
    assert "--allowedTools" not in cmd
    assert "--add-dir" not in cmd


# ---- lenient JSON extraction -------------------------------------------------

def test_invoke_extracts_json_when_embedded_in_chatter(monkeypatch):
    mod = _load()
    chatter = (
        "Sure! I reviewed the diff. Here's the structured result:\n\n"
        '{"findings": [{"file": "src/auth/token.py", "line": 12}], "count": 1}\n\n'
        "Let me know if you want more detail."
    )
    run = mock.Mock(return_value=_completed(chatter))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("review the diff")

    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["findings"][0]["file"] == "src/auth/token.py"
    assert result["raw"] == chatter


def test_invoke_extracts_array_payload_when_wrapped_in_prose(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed(
        'Here are the scores: [3, 7, 9] — computed per file.'
    ))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("score each file")

    assert result["ok"] is True
    assert result["data"] == [3, 7, 9]


def test_extract_json_block_ignores_braces_inside_strings():
    mod = _load()

    data = mod.extract_json_block('noise {"msg": "brace } in string", "n": 2} tail')

    assert data == {"msg": "brace } in string", "n": 2}


def test_extract_json_block_returns_none_when_no_json():
    mod = _load()

    assert mod.extract_json_block("no structured payload here") is None


def test_extract_json_block_prefers_last_block_over_echoed_example():
    mod = _load()
    text = (
        'Following the example format {"file": "example.py", "line": 1}, '
        "here is my actual finding:\n"
        '{"file": "src/auth/token.py", "line": 88}'
    )

    data = mod.extract_json_block(text)

    assert data == {"file": "src/auth/token.py", "line": 88}


def test_extract_json_block_returns_whole_last_payload_not_its_nested_child():
    mod = _load()

    data = mod.extract_json_block(
        'result: {"outer": {"inner": 1}, "n": 2} done')

    assert data == {"outer": {"inner": 1}, "n": 2}


def test_extract_json_block_degrades_to_earlier_block_when_last_malformed():
    mod = _load()

    data = mod.extract_json_block(
        '{"verdict": "pass"} and then some cut-off {"trunca')

    assert data == {"verdict": "pass"}


# ---- retries -----------------------------------------------------------------

def test_invoke_retries_exactly_once_when_first_output_malformed(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=[
        _completed("hmm, I couldn't produce JSON this time"),
        _completed('{"verdict": "pass"}'),
    ])
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("classify the change", retries=1)

    assert run.call_count == 2  # first malformed → exactly one retry
    assert result["ok"] is True
    assert result["data"] == {"verdict": "pass"}


def test_invoke_returns_ok_false_when_output_malformed_after_retries(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("still just prose, no JSON at all"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("classify the change", retries=1)

    assert run.call_count == 2  # initial attempt + 1 retry, then give up
    assert result["ok"] is False
    assert result["data"] is None
    assert "no parsable JSON" in result["error"]
    assert result["raw"] == "still just prose, no JSON at all"


def test_invoke_does_not_retry_when_retries_zero(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("not json"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("one shot only", retries=0)

    assert run.call_count == 1
    assert result["ok"] is False


# ---- raw mode ----------------------------------------------------------------

def test_invoke_raw_mode_accepts_prose_output_without_burning_retries(monkeypatch):
    mod = _load()
    prose = "The diff looks correct. One nit: rename `tmp` to `staging_dir`."
    run = mock.Mock(return_value=_completed(prose))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("review this diff in prose", raw=True, retries=3)

    assert run.call_count == 1  # non-JSON output is NOT malformed in raw mode
    assert result["ok"] is True
    assert result["data"] is None
    assert result["raw"] == prose
    assert result["error"] is None


def test_invoke_raw_mode_fails_on_empty_stdout_without_retry(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed("   \n"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("anything", raw=True, retries=3)

    assert run.call_count == 1
    assert result["ok"] is False
    assert "empty" in result["error"]


# ---- timeout / model resolution ----------------------------------------------

def test_timeout_env_var_beats_settings_file(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path, {"kimi.timeout_secs": 120})
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "45")
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me")

    assert run.call_args.kwargs["timeout"] == 45


def test_timeout_settings_file_beats_builtin_default(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path, {"kimi.timeout_secs": 120})
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me")

    assert run.call_args.kwargs["timeout"] == 120


def test_timeout_defaults_to_300_when_unconfigured(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path)  # .ucw root exists but no settings.json
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me")

    assert run.call_args.kwargs["timeout"] == 300


def test_explicit_timeout_beats_env_and_settings(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path, {"kimi.timeout_secs": 120})
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "45")
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me", timeout=7)

    assert run.call_args.kwargs["timeout"] == 7


def test_model_from_settings_is_exported_as_kimi_model_env(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path, {"kimi.model": "kimi-k3-turbo"})
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    monkeypatch.setenv("UCW_SENTINEL_FOR_TEST", "kept")
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me")

    env = run.call_args.kwargs["env"]
    assert env["KIMI_MODEL"] == "kimi-k3-turbo"
    assert env["UCW_SENTINEL_FOR_TEST"] == "kept"  # os.environ is inherited


def test_model_env_var_beats_settings_file(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path, {"kimi.model": "kimi-k3-turbo"})
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    monkeypatch.setenv("UCW_KIMI_MODEL", "kimi-k2.6")
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me")

    assert run.call_args.kwargs["env"]["KIMI_MODEL"] == "kimi-k2.6"


def test_model_defaults_to_kimi_k3_when_unconfigured(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me")

    assert run.call_args.kwargs["env"]["KIMI_MODEL"] == "kimi-k3"


def test_explicit_model_argument_beats_env(monkeypatch, tmp_path):
    mod = _load()
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    _isolate_resolution_env(monkeypatch)
    monkeypatch.setenv("UCW_KIMI_MODEL", "kimi-k2.6")
    run = mock.Mock(return_value=_completed("{}"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    mod.kimi_invoke("resolve me", model="kimi-k3-preview")

    assert run.call_args.kwargs["env"]["KIMI_MODEL"] == "kimi-k3-preview"


# ---- failure modes never raise ----------------------------------------------

def test_invoke_returns_ok_false_when_subprocess_times_out(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=subprocess.TimeoutExpired(
        cmd=["claude-kimi", "-p", "x"], timeout=90,
    ))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("slow prompt", timeout=90)

    assert result["ok"] is False
    assert result["data"] is None
    assert "timed out" in result["error"]
    assert run.call_count == 1  # timeouts are not retried


def test_invoke_returns_ok_false_when_exit_nonzero(monkeypatch):
    mod = _load()
    run = mock.Mock(return_value=_completed(
        "", returncode=1, stderr="auth expired",
    ))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("anything")

    assert result["ok"] is False
    assert "exited 1" in result["error"]
    assert "auth expired" in result["error"]


def test_invoke_returns_ok_false_when_binary_missing(monkeypatch):
    mod = _load()
    run = mock.Mock(side_effect=FileNotFoundError("claude-kimi"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    result = mod.kimi_invoke("anything")

    assert result["ok"] is False
    assert "not found" in result["error"]


# ---- standalone entry point --------------------------------------------------

def test_main_exits_zero_and_prints_result_json_on_success(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed('{"answer": 42}'))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["what is the answer", "--timeout", "5"])

    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["ok"] is True
    assert body["data"] == {"answer": 42}


def test_main_exits_nonzero_on_failure(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed("no json", returncode=0))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["prompt", "--retries", "0"])

    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert body["ok"] is False


def test_main_reads_prompt_from_stdin_when_dash(monkeypatch, capsys):
    mod = _load()
    diff = "diff --git a/src/auth/token.py b/src/auth/token.py\n+    verify(sig)\n"
    run = mock.Mock(return_value=_completed('{"verdict": "pass"}'))
    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(diff))

    rc = mod.main(["-", "--timeout", "5"])

    cmd = run.call_args.args[0]
    assert cmd[:2] == ["claude-kimi", "-p"]
    assert cmd[2] == diff  # the piped diff, not the literal "-"
    assert rc == 0


def test_main_forwards_raw_flag_and_model_option(monkeypatch, capsys):
    mod = _load()
    run = mock.Mock(return_value=_completed("plain prose verdict, no JSON"))
    monkeypatch.setattr(mod.subprocess, "run", run)

    rc = mod.main(["summarize", "--raw", "--model", "kimi-k3-turbo",
                   "--timeout", "5"])

    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["ok"] is True
    assert body["data"] is None
    assert body["raw"] == "plain prose verdict, no JSON"
    assert run.call_args.kwargs["env"]["KIMI_MODEL"] == "kimi-k3-turbo"
