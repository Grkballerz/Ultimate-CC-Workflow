"""Tests for the worktree helper.

We build a real git repo, spin up worktrees, and assert the create/list/cleanup/remove
flow. All ops run with commit.gpgsign disabled per-repo (CI environments may force it).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_worktree", REPO_ROOT / "bin" / "ucw-worktree.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_worktree"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _git(*args, cwd: Path):
    subprocess.check_call(["git", *args], cwd=cwd)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git("init", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "test@example.com", cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=tmp_path)
    _git("config", "commit.gpgsign", "false", cwd=tmp_path)
    _git("config", "tag.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "README.md").write_text("# x\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_parse_max_age():
    mod = _load()
    assert mod._parse_max_age("30s") == 30
    assert mod._parse_max_age("5m") == 300
    assert mod._parse_max_age("2h") == 7200
    assert mod._parse_max_age("1d") == 86400


def test_parse_max_age_invalid():
    mod = _load()
    with pytest.raises(ValueError):
        mod._parse_max_age("forever")


def test_slugify():
    mod = _load()
    assert mod._slugify("Add /healthz endpoint!") == "add-healthz-endpoint"
    assert mod._slugify("") == "task"


def test_create_then_list(repo, capsys):
    mod = _load()
    mod.main(["create", "demo task"])
    meta = json.loads(capsys.readouterr().out)
    assert meta["slug"] == "demo-task"
    assert "ucw/wt/demo-task" in meta["branch"]
    wt_path = Path(meta["path"])
    assert wt_path.is_dir()
    assert (wt_path / ".ucw" / "worktree-meta.json").exists()

    mod.main(["list", "--json"])
    entries = json.loads(capsys.readouterr().out)
    branches = [e.get("branch", "") for e in entries]
    assert any("demo-task" in b for b in branches)


def test_remove(repo, capsys):
    mod = _load()
    mod.main(["create", "kill-me"])
    capsys.readouterr()

    mod.main(["remove", "kill-me"])
    out = capsys.readouterr().out
    assert "removed" in out

    mod.main(["list", "--json"])
    entries = json.loads(capsys.readouterr().out)
    branches = [e.get("branch", "") for e in entries]
    assert not any("kill-me" in b for b in branches)


def test_cleanup_skips_recent(repo, capsys):
    mod = _load()
    mod.main(["create", "fresh"])
    capsys.readouterr()

    mod.main(["cleanup", "--max-age", "1d", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["removed"] == []
    assert any("fresh" in s for s in body["skipped"])
