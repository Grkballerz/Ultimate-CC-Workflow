"""Cover the lower-priority paths in ucw-help.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_help_more", REPO_ROOT / "bin" / "ucw-help.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_help_more"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_hooks_subcommand_lists_event_hooks(capsys):
    mod = _load()
    mod.main(["hooks"])
    out = capsys.readouterr().out
    # Expect at least these hook scripts (sans _hook_common.py)
    for name in ("post-tool-use.py", "session-start.py", "stop.py"):
        assert name in out
    # _hook_common.py is a library helper and should be filtered out
    assert "_hook_common.py" not in out


def test_skills_empty_message(tmp_path, monkeypatch, capsys):
    """When no skills are installed yet, show a friendly hint."""
    mod = _load()
    # Point REPO at an empty tree
    fake_repo = tmp_path / "fake"
    (fake_repo / "skills").mkdir(parents=True)
    monkeypatch.setattr(mod, "REPO", fake_repo)
    mod.cmd_skills(None)
    out = capsys.readouterr().out
    assert "none yet" in out
    assert "/distill" in out


def test_repo_root_resolves_via_symlink(tmp_path, monkeypatch):
    """When UCW_HOME points at an install with a bin/ucw-help.py symlink, the
    helper should resolve the symlink target to discover the source repo."""
    fake_home = tmp_path / "fake-home"
    fake_bin = fake_home / "bin"
    fake_bin.mkdir(parents=True)
    target = REPO_ROOT / "bin" / "ucw-help.py"
    (fake_bin / "ucw-help.py").symlink_to(target)
    monkeypatch.setenv("UCW_HOME", str(fake_home))

    # Re-import to pick up the env var at module-load time
    import importlib.util as iu
    spec = iu.spec_from_file_location("ucw_help_resolve", target)
    mod = iu.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    # Symlink resolution should return the source repo (parent of bin/)
    assert mod._repo_root() == REPO_ROOT


def test_help_main_returns_two_for_missing_subparser(capsys):
    """argparse returns 2 when given an unrecognized subcommand."""
    mod = _load()
    with pytest.raises(SystemExit) as e:
        mod.main(["nosuchtopic"])
    assert e.value.code == 2
