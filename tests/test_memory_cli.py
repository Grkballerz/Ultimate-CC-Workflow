"""Tests for the memory CLI (note/recall/pin/forget/list/stats/distill)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from ucw_memory import cli as memcli


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".ucw").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCW_MEMORY_HOME", str(tmp_path / ".claude" / "ucw"))
    return tmp_path


def _run(*args, capsys):
    rc = memcli.main(list(args))
    return rc, capsys.readouterr().out


def test_init_creates_db(project, capsys):
    rc, out = _run("init", capsys=capsys)
    body = json.loads(out)
    assert rc == 0
    assert body["ok"] is True
    assert Path(body["db_path"]).exists()


def test_note_then_recall(project, capsys):
    _run("init", capsys=capsys)
    rc, out = _run("note", "demo-app", "uses", "flask",
                   "--reason", "quick prototype", capsys=capsys)
    json.loads(out)["id"]
    assert rc == 0

    rc, out = _run("recall", "flask", capsys=capsys)
    body = json.loads(out)
    assert rc == 0
    assert any("flask" in h["text"] for h in body["hits"])


def test_pin_and_list(project, capsys):
    _run("init", capsys=capsys)
    _rc, out = _run("note", "demo", "uses", "redis",
                    "--reason", "cache", "--pinned", capsys=capsys)
    fid = json.loads(out)["id"]

    _rc, out = _run("list", capsys=capsys)
    body = json.loads(out)
    found = [f for f in body if f["id"] == fid]
    assert found and found[0]["pinned"]


def test_forget_removes_from_results(project, capsys):
    _run("init", capsys=capsys)
    _rc, out = _run("note", "x", "is", "junk", "--reason", "wrong",
                    capsys=capsys)
    fid = json.loads(out)["id"]

    _run("forget", str(fid), capsys=capsys)

    _rc, out = _run("recall", "junk", capsys=capsys)
    body = json.loads(out)
    assert not any("junk" in h["text"] for h in body["hits"])


def test_stats(project, capsys):
    _run("init", capsys=capsys)
    _run("note", "a", "is", "b", "--reason", "c", capsys=capsys)
    _rc, out = _run("stats", capsys=capsys)
    body = json.loads(out)
    assert body["total"] >= 1


def test_distill_dry_run(project, capsys, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({
        "message": {"content": "We chose to use bun because it's fast."}
    }))
    _run("init", capsys=capsys)
    rc, out = _run("distill", str(transcript), "--dry-run", capsys=capsys)
    body = json.loads(out)
    assert rc == 0
    assert any("bun" in c["object"].lower() for c in body)


def test_distill_writes(project, capsys, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({
        "message": {"content": "We chose to use bun because it's fast."}
    }))
    _run("init", capsys=capsys)
    rc, out = _run("distill", str(transcript), capsys=capsys)
    body = json.loads(out)
    assert rc == 0
    assert body["candidates"] >= 1
    assert body["written"] >= 1
