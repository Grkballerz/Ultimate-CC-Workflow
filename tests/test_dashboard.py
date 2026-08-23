"""Tests for the dashboard CLI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dashboard import cli as dash_cli


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "knowledge").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("UCW_AUTO_MODE", raising=False)
    monkeypatch.delenv("UCW_AUTO_RETRY_CAP", raising=False)
    return tmp_path


def test_status_no_ucw_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = dash_cli.main(["status"])
    assert rc == 1


def test_status_json_shape(project, capsys):
    (project / ".ucw" / "state" / "phase").write_text("build\n")
    (project / ".ucw" / "state" / "edit-streak").write_text("3\n")
    rc = dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["phase"] == "build"
    assert body["edit_streak"] == 3
    assert body["embedding_mode"] == "fts-only"
    assert body["knowledge"]["exists"] is True


def test_status_reports_embedding_mode_claude(project, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["embedding_mode"] == "fts+rerank-claude"


def test_status_reports_embedding_mode_voyage(project, monkeypatch, capsys):
    monkeypatch.setenv("VOYAGE_API_KEY", "vy-fake")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["embedding_mode"] == "fts+rerank-voyage"


def test_status_plain_text_no_color(project, capsys):
    rc = dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "\033[" not in out  # no ANSI sequences
    assert "UCW status" in out
    assert rc == 0


def test_status_shows_stale_when_pkg_newer(project, capsys, monkeypatch):
    import os
    import time
    knowledge = project / ".ucw" / "knowledge"
    stack = knowledge / "STACK.md"
    stack.write_text("# Stack\n")
    old = time.time() - 1000
    os.utime(stack, (old, old))
    (project / "package.json").write_text('{"name":"x"}')

    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert any(s["doc"] == "STACK.md" for s in body["stale"])


def test_status_stale_hint_points_to_ucw_scribe(project, capsys):
    """The scribe lives under /ucw — a bare /scribe hint points at nothing."""
    import os
    import time
    stack = project / ".ucw" / "knowledge" / "STACK.md"
    stack.write_text("# Stack\n")
    old = time.time() - 1000
    os.utime(stack, (old, old))
    (project / "package.json").write_text('{"name":"x"}')

    dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "run /ucw scribe" in out


# ---- auto-mode block --------------------------------------------------------

def test_status_auto_mode_off_by_default(project, capsys):
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["auto_mode"] == {"enabled": False, "level": 0}


def test_status_auto_mode_reads_ucw_auto_state(project, capsys):
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps(
        {"level": 3, "since": "2026-08-21T00:00:00Z", "retry_cap": 5}
    ))
    (project / ".ucw" / "state" / "auto-retries").write_text("2")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    auto = body["auto_mode"]
    assert auto["enabled"] is True
    assert auto["level"] == 3
    assert auto["since"] == "2026-08-21T00:00:00Z"
    assert auto["retry_cap"] == 5
    assert auto["retries_used"] == 2


def test_status_auto_mode_env_override_wins(project, capsys, monkeypatch):
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 4}))
    monkeypatch.setenv("UCW_AUTO_MODE", "off")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["auto_mode"]["enabled"] is False


def test_status_auto_mode_retry_cap_falls_back_to_setting(project, capsys):
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 2}))
    (project / ".ucw" / "state" / "settings.json").write_text(
        json.dumps({"auto.retry_cap": 7})
    )
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["auto_mode"]["retry_cap"] == 7


def test_status_auto_mode_in_plain_text(project, capsys):
    (project / ".ucw" / "state" / "auto-mode").write_text(json.dumps({"level": 2}))
    dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "auto mode" in out
    assert "level 2" in out
    assert "retries 0/3" in out


# ---- review-gate block ------------------------------------------------------

_SHA = "a" * 40


def _finding(fid: str, severity: str, **extra) -> dict:
    base = {
        "id": fid, "severity": severity, "category": "correctness",
        "file": "src/x.py", "line": 3, "title": f"finding {fid}",
        "detail": "details", "sha": _SHA,
    }
    base.update(extra)
    return base


def test_status_review_gate_none_when_no_reviews(project, capsys):
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["review_gate"] is None


def test_status_review_gate_none_when_head_not_reviewed(project, capsys, monkeypatch):
    (project / ".ucw" / "reviews" / ("b" * 40)).mkdir(parents=True)
    monkeypatch.setattr(dash_cli, "_head_sha", lambda repo: _SHA)
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["review_gate"] is None


def test_status_review_gate_counts_for_head_sha(project, capsys, monkeypatch):
    monkeypatch.setattr(dash_cli, "_head_sha", lambda repo: _SHA)
    rdir = project / ".ucw" / "reviews" / _SHA
    rdir.mkdir(parents=True)
    rows = [_finding("f-1", "critical"), _finding("f-2", "minor")]
    (rdir / "findings.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    gate = body["review_gate"]
    assert gate["sha"] == _SHA
    assert gate["open_findings"] == 2
    assert gate["effective_critical"] == 1
    assert gate["unack_critical"] == 1


def test_status_review_gate_uses_effective_severity(project, capsys, monkeypatch):
    """A refuted critical drops two levels — it must not count as critical."""
    monkeypatch.setattr(dash_cli, "_head_sha", lambda repo: _SHA)
    rdir = project / ".ucw" / "reviews" / _SHA
    rdir.mkdir(parents=True)
    rows = [
        _finding("f-1", "critical"),
        _finding("f-1", "critical", disprover_verdict="refuted"),  # latest wins
    ]
    (rdir / "findings.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    gate = body["review_gate"]
    assert gate["open_findings"] == 1
    assert gate["effective_critical"] == 0


def test_status_review_gate_in_plain_text(project, capsys, monkeypatch):
    monkeypatch.setattr(dash_cli, "_head_sha", lambda repo: _SHA)
    rdir = project / ".ucw" / "reviews" / _SHA
    rdir.mkdir(parents=True)
    (rdir / "findings.jsonl").write_text(json.dumps(_finding("f-1", "major")) + "\n")
    dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "review gate" in out
    assert "1 open finding(s), 0 effective-critical" in out


def test_status_review_gate_none_in_plain_text(project, capsys):
    dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "review gate       none" in out


def test_head_sha_runs_git_rev_parse(project, monkeypatch):
    calls = {}

    class _CP:
        returncode = 0
        stdout = "abc123\n"

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["cwd"] = kwargs.get("cwd")
        return _CP()

    monkeypatch.setattr(dash_cli.subprocess, "run", fake_run)
    assert dash_cli._head_sha(project) == "abc123"
    assert calls["cmd"] == ["git", "rev-parse", "HEAD"]
    assert calls["cwd"] == project


def test_head_sha_none_when_git_fails(project, monkeypatch):
    class _CP:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(dash_cli.subprocess, "run", lambda *a, **k: _CP())
    assert dash_cli._head_sha(project) is None


# ---- distill-health block ---------------------------------------------------

def _session_line(jobs: int, facts: int) -> str:
    return (f"1755740000\tsess-1\tother\t"
            f"distill_jobs={jobs}\tfacts_written={facts}")


def test_status_distill_warning_after_three_zero_batches(project, capsys):
    (project / ".ucw" / "sessions.log").write_text(
        "\n".join(_session_line(2, 0) for _ in range(3)) + "\n"
    )
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    health = body["distill_health"]
    assert health["batches"] == 3
    assert health["zero_streak"] == 3
    assert health["warning"] == (
        "memory distill has extracted 0 facts in 3 batches — "
        "pipeline may be broken"
    )


def test_status_distill_no_warning_when_recent_batch_wrote_facts(project, capsys):
    lines = [_session_line(2, 0), _session_line(2, 0), _session_line(1, 4)]
    (project / ".ucw" / "sessions.log").write_text("\n".join(lines) + "\n")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["distill_health"]["warning"] is None
    assert body["distill_health"]["zero_streak"] == 0


def test_status_distill_no_warning_under_three_batches(project, capsys):
    lines = [_session_line(2, 0), _session_line(1, 0)]
    (project / ".ucw" / "sessions.log").write_text("\n".join(lines) + "\n")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["distill_health"]["zero_streak"] == 2
    assert body["distill_health"]["warning"] is None


def test_status_distill_ignores_sessions_with_no_jobs(project, capsys):
    """Sessions that had nothing queued prove nothing about the pipeline."""
    lines = [_session_line(0, 0) for _ in range(5)]
    (project / ".ucw" / "sessions.log").write_text("\n".join(lines) + "\n")
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["distill_health"]["batches"] == 0
    assert body["distill_health"]["warning"] is None


def test_status_distill_missing_log_is_healthy(project, capsys):
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["distill_health"] == {
        "batches": 0, "zero_streak": 0, "warning": None,
    }


def test_status_distill_warning_in_plain_text(project, capsys):
    (project / ".ucw" / "sessions.log").write_text(
        "\n".join(_session_line(1, 0) for _ in range(4)) + "\n"
    )
    dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "distill health" in out
    assert "extracted 0 facts in 4 batches" in out
