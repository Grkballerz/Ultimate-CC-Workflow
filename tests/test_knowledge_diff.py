"""Tests for the scribe diff helper. Builds a tiny git repo, commits, and
asserts classification."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_knowledge_diff", REPO_ROOT / "bin" / "ucw-knowledge-diff.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_knowledge_diff"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _git(*args, cwd: Path):
    subprocess.check_call(["git", *args], cwd=cwd)


def _init_repo(tmp_path: Path):
    _git("init", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "test@example.com", cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=tmp_path)
    # Disable GPG signing — the test environment may have it enabled globally.
    _git("config", "commit.gpgsign", "false", cwd=tmp_path)
    _git("config", "tag.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "README.md").write_text("# x\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)


def test_classify_package_json_hits_stack(tmp_path, capsys):
    mod = _load()
    _init_repo(tmp_path)
    (tmp_path / "package.json").write_text('{"name":"x","dependencies":{"next":"14"}}')
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "add deps", cwd=tmp_path)

    rc = mod.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert rc == 0
    assert "STACK.md" in body["suggested_updates"]
    assert any("package.json" in r for r in body["suggested_updates"]["STACK.md"])


def test_src_dir_change_hits_design(tmp_path, capsys):
    mod = _load()
    _init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello(): pass\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "add code", cwd=tmp_path)

    mod.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert "DESIGN.md" in body["suggested_updates"]


def test_readme_change_is_noise(tmp_path, capsys):
    mod = _load()
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("# x\n# y\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "edit readme", cwd=tmp_path)

    mod.main(["--repo", str(tmp_path)])
    body = json.loads(capsys.readouterr().out)
    # The path is in `changed` but classified to no docs
    assert body["suggested_updates"] == {}


def test_uses_last_scribe_sha_when_present(tmp_path, capsys):
    mod = _load()
    _init_repo(tmp_path)
    sha0 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    (tmp_path / "x.py").write_text("x = 1\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "x", cwd=tmp_path)
    (tmp_path / "package.json").write_text('{"name":"x"}')
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "y", cwd=tmp_path)

    ucw = tmp_path / ".ucw"
    ucw.mkdir()
    (ucw / "last-scribe-sha").write_text(sha0)

    mod.main(["--repo", str(tmp_path)])
    body = json.loads(capsys.readouterr().out)
    assert body["since"] == sha0
    # Should see both files changed since sha0
    paths = {c["path"] for c in body["changed"]}
    assert "x.py" in paths
    assert "package.json" in paths
