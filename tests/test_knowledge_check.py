"""Tests for the stale-doc detector."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_knowledge_check", REPO_ROOT / "bin" / "ucw-knowledge-check.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_knowledge_check"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _setup_project(tmp_path: Path) -> Path:
    (tmp_path / ".ucw" / "knowledge").mkdir(parents=True)
    return tmp_path


def _touch(path: Path, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_fresh_repo_no_warnings(tmp_path):
    mod = _load()
    _setup_project(tmp_path)
    # No knowledge docs yet
    assert mod.check(tmp_path) == []


def test_stale_stack_when_pkg_is_newer(tmp_path):
    mod = _load()
    _setup_project(tmp_path)
    old = time.time() - 100
    new = time.time()
    _touch(tmp_path / ".ucw" / "knowledge" / "STACK.md", mtime=old)
    _touch(tmp_path / "package.json", mtime=new)

    stale = mod.check(tmp_path)
    docs = [s.doc for s in stale]
    assert "STACK.md" in docs


def test_stale_design_when_src_changes(tmp_path):
    mod = _load()
    _setup_project(tmp_path)
    old = time.time() - 100
    new = time.time()
    _touch(tmp_path / ".ucw" / "knowledge" / "DESIGN.md", mtime=old)
    _touch(tmp_path / "src" / "app.py", mtime=new)

    stale = mod.check(tmp_path)
    assert any(s.doc == "DESIGN.md" for s in stale)


def test_fresh_when_doc_is_newer(tmp_path):
    mod = _load()
    _setup_project(tmp_path)
    old = time.time() - 100
    new = time.time()
    _touch(tmp_path / "package.json", mtime=old)
    _touch(tmp_path / ".ucw" / "knowledge" / "STACK.md", mtime=new)

    stale = mod.check(tmp_path)
    assert not any(s.doc == "STACK.md" for s in stale)


def test_strict_returns_nonzero(tmp_path, capsys):
    mod = _load()
    _setup_project(tmp_path)
    old = time.time() - 100
    new = time.time()
    _touch(tmp_path / ".ucw" / "knowledge" / "STACK.md", mtime=old)
    _touch(tmp_path / "package.json", mtime=new)

    rc = mod.main(["--repo", str(tmp_path), "--strict"])
    assert rc == 1


def test_json_output(tmp_path, capsys):
    mod = _load()
    _setup_project(tmp_path)
    old = time.time() - 100
    new = time.time()
    _touch(tmp_path / ".ucw" / "knowledge" / "STACK.md", mtime=old)
    _touch(tmp_path / "package.json", mtime=new)

    mod.main(["--repo", str(tmp_path), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert any(s["doc"] == "STACK.md" for s in body)
