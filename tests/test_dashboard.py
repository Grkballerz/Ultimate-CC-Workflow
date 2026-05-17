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
