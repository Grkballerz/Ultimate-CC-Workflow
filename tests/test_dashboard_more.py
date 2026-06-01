"""Additional dashboard coverage — error paths, color rendering, all flags."""
from __future__ import annotations

import json
import os
import sys
import time
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
    return tmp_path


def test_status_streak_yellow_at_3(project, capsys):
    (project / ".ucw" / "state" / "edit-streak").write_text("3")
    rc = dash_cli.main(["status"])
    out = capsys.readouterr().out
    # When stdout is not a TTY in pytest, paint() returns plain text.
    # The "breaker fires at 5" hint should still appear because streak >= 3.
    assert "breaker fires at 5" in out
    assert rc == 0


def test_status_color_renders_when_isatty(project, capsys, monkeypatch):
    """Force isatty=True and assert ANSI is emitted."""
    (project / ".ucw" / "state" / "phase").write_text("build")
    (project / ".ucw" / "state" / "edit-streak").write_text("6")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    dash_cli.main(["status"])
    out = capsys.readouterr().out
    assert "\033[" in out  # ANSI codes present


def test_status_handles_missing_knowledge_dir(tmp_path, monkeypatch, capsys):
    (tmp_path / ".ucw").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    rc = dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "knowledge docs    (none — run /ucw init)" in out
    assert rc == 0


def test_status_error_json_when_no_ucw(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "error" in body


def test_status_handles_unreadable_streak(project, capsys, monkeypatch):
    """Garbage in state/edit-streak shouldn't crash; streak becomes 0."""
    (project / ".ucw" / "state" / "edit-streak").write_text("notanint")
    rc = dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["edit_streak"] == 0


def test_status_stale_warning_in_plain_text(project, capsys, monkeypatch):
    knowledge = project / ".ucw" / "knowledge"
    stack = knowledge / "STACK.md"
    stack.write_text("# Stack\n")
    old = time.time() - 1000
    os.utime(stack, (old, old))
    (project / "package.json").write_text('{"name":"x"}')
    rc = dash_cli.main(["status", "--no-color"])
    out = capsys.readouterr().out
    assert "stale docs" in out
    assert "need refresh" in out
    assert rc == 0


def test_status_global_memory_is_separately_reported(project, monkeypatch, capsys):
    monkeypatch.setenv("UCW_MEMORY_HOME", str(project / "alt-global"))
    dash_cli.main(["status", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert "global" in body["memory"]
    assert "project" in body["memory"]
