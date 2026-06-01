"""Tests for the ucw-help discovery tool."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_help", REPO_ROOT / "bin" / "ucw-help.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_help"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_overview_includes_counts(capsys):
    mod = _load()
    rc = mod.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "agents:" in out
    assert "commands:" in out
    assert "hooks:" in out


def test_agents_lists_all(capsys):
    mod = _load()
    mod.main(["agents"])
    out = capsys.readouterr().out
    for expected in ("planner", "implementer", "verifier", "reviewer",
                     "security-reviewer", "memory-curator", "scribe", "onboarder"):
        assert expected in out


def test_commands_lists_umbrella(capsys):
    """All UCW commands now live under `/ucw`. The help listing should surface
    the single root; subcommand documentation lives in the command file body
    (asserted by test_review_agents.py / test_no_builtin_collisions.py).
    """
    mod = _load()
    mod.main(["commands"])
    out = capsys.readouterr().out
    assert "/ucw" in out


def test_skills_lists_promoted(capsys):
    mod = _load()
    mod.main(["skills"])
    out = capsys.readouterr().out
    # We shipped at least 4 reference skills
    assert any(name in out for name in ("tdd-loop", "git-worktree", "memory-recall", "commit-discipline"))


def test_bin_includes_help_itself(capsys):
    mod = _load()
    mod.main(["bin"])
    out = capsys.readouterr().out
    assert "ucw-help.py" in out
    assert "ucw-audit.py" in out
