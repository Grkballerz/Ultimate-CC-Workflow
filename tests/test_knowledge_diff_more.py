"""Additional coverage for ucw-knowledge-diff.py."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_knowledge_diff_more", REPO_ROOT / "bin" / "ucw-knowledge-diff.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_knowledge_diff_more"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _git(*args, cwd):
    subprocess.check_call(["git", *args], cwd=cwd)


def _init_repo(tmp_path):
    _git("init", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "t@t.t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    _git("config", "commit.gpgsign", "false", cwd=tmp_path)
    _git("config", "tag.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "README.md").write_text("# x\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)


def test_not_a_git_repo(tmp_path, capsys):
    mod = _load()
    rc = mod.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert rc == 2
    assert "not a git repo" in body["error"]


def test_first_run_with_only_one_commit(tmp_path, capsys):
    """No .ucw/last-scribe-sha and no HEAD~1 → empty result, exit 0."""
    mod = _load()
    _init_repo(tmp_path)  # exactly 1 commit
    rc = mod.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert rc == 0
    assert body["since"] is None
    assert body["changed"] == []


def test_explicit_since_ref(tmp_path, capsys):
    mod = _load()
    _init_repo(tmp_path)
    sha0 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.py").write_text("x = 1\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "add code", cwd=tmp_path)

    rc = mod.main(["--repo", str(tmp_path), "--since", sha0])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "DESIGN.md" in body["suggested_updates"]


def test_eslintrc_classified_as_conventions(tmp_path, capsys):
    mod = _load()
    _init_repo(tmp_path)
    (tmp_path / ".eslintrc.json").write_text("{}")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "lint", cwd=tmp_path)
    mod.main(["--repo", str(tmp_path)])
    body = json.loads(capsys.readouterr().out)
    assert "CONVENTIONS.md" in body["suggested_updates"]


def test_noise_paths_dropped(tmp_path, capsys):
    """LICENSE and .gitignore should produce no suggested updates."""
    mod = _load()
    _init_repo(tmp_path)
    (tmp_path / "LICENSE").write_text("MIT\n")
    (tmp_path / ".gitignore").write_text("*.pyc\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "license", cwd=tmp_path)
    mod.main(["--repo", str(tmp_path)])
    body = json.loads(capsys.readouterr().out)
    assert body["suggested_updates"] == {}
