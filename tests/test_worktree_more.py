"""Additional worktree coverage — cleanup with --force, no-match remove,
error paths."""
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
        "ucw_worktree_more", REPO_ROOT / "bin" / "ucw-worktree.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_worktree_more"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _git(*args, cwd):
    subprocess.check_call(["git", *args], cwd=cwd)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git("init", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "t@t.t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    _git("config", "commit.gpgsign", "false", cwd=tmp_path)
    _git("config", "tag.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "README.md").write_text("# x\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_remove_unknown_slug_returns_2(repo, capsys):
    mod = _load()
    rc = mod.main(["remove", "no-such-worktree"])
    assert rc == 2


def test_cleanup_with_force_removes_recent(repo, capsys):
    mod = _load()
    mod.main(["create", "force-me"])
    capsys.readouterr()
    # No --max-age cap → all UCW worktrees eligible
    mod.main(["cleanup", "--force", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert any("force-me" in r for r in body["removed"])


def test_cleanup_skips_with_unpushed_commits(repo, capsys):
    mod = _load()
    mod.main(["create", "with-work"])
    meta = json.loads(capsys.readouterr().out)
    wt = Path(meta["path"])

    # Make a commit inside the worktree
    _git("config", "user.email", "t@t.t", cwd=wt)
    _git("config", "user.name", "t", cwd=wt)
    _git("config", "commit.gpgsign", "false", cwd=wt)
    (wt / "new.txt").write_text("work")
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", "wip", cwd=wt)

    # Cleanup without --force: should skip
    mod.main(["cleanup", "--max-age", "0s", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert any("unpushed" in s for s in body["skipped"])


def test_list_no_ucw_worktrees_prints_message(repo, capsys):
    mod = _load()
    mod.main(["list"])
    out = capsys.readouterr().out
    assert "no UCW worktrees" in out


def test_create_with_explicit_base(repo, capsys):
    mod = _load()
    mod.main(["create", "branched-from-main", "--base", "main"])
    meta = json.loads(capsys.readouterr().out)
    assert meta["parent_ref"] == "main"
