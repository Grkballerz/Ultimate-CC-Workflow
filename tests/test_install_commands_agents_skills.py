"""Regression tests for install_commands / install_agents / install_skills.

Before this fix, the installer copied rules + hooks + bin but never linked
the actual commands, agents, or skills into the Claude Code discovery dirs
(~/.claude/{commands,agents,skills}). Slash commands like /ucw, subagents
like reviewer-injection, and shipped skills like tdd-loop were unreachable
no matter what you typed.

These tests lock in:
  1. install_commands symlinks every commands/*.md into ~/.claude/commands/
  2. install_agents flat-links every .md under agents/ (incl. agents/reviewers/*)
  3. install_skills symlinks each SKILL.md's parent dir into ~/.claude/skills/
  4. uninstall removes ONLY the symlinks we created, preserving user files
  5. minimal profile keeps it lean (no skills, no hooks)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install.sh"


def _has(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("UCW_HOME", str(tmp_path / ".claude" / "ucw"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / ".claude"))
    return tmp_path


def _run(*args, fake_home: Path) -> tuple[int, str, str]:
    cp = subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True, text=True, env=os.environ.copy(), timeout=180,
    )
    return cp.returncode, cp.stdout, cp.stderr


# ---- commands --------------------------------------------------------------

@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_ucw_command_linked_at_user_level(fake_home):
    rc, out, err = _run("--profile", "minimal", fake_home=fake_home)
    assert rc == 0, f"{out}\n{err}"
    ucw_cmd = fake_home / ".claude" / "commands" / "ucw.md"
    assert ucw_cmd.is_symlink(), "ucw.md must be a symlink in ~/.claude/commands"
    # Points back to the repo, so `git pull` updates it
    target = os.readlink(ucw_cmd)
    assert "commands/ucw.md" in target, f"unexpected symlink target: {target}"


# ---- agents ----------------------------------------------------------------

@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_all_top_level_agents_linked(fake_home):
    _run("--profile", "minimal", fake_home=fake_home)
    agents_dir = fake_home / ".claude" / "agents"
    expected = ["planner", "implementer", "verifier", "reviewer",
                "security-reviewer", "memory-curator", "scribe", "onboarder",
                "disprover", "reachability"]
    for name in expected:
        f = agents_dir / f"{name}.md"
        assert f.is_symlink(), f"agent {name}.md not linked into ~/.claude/agents/"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_narrow_reviewer_agents_flattened(fake_home):
    """agents/reviewers/{concern}.md must all end up at ~/.claude/agents/{concern}.md
    so Claude Code's flat agent loader picks them up."""
    _run("--profile", "minimal", fake_home=fake_home)
    agents_dir = fake_home / ".claude" / "agents"
    for concern in ("correctness", "injection", "deserialization", "auth",
                    "performance", "data-loss", "api-compat", "tests", "docs"):
        f = agents_dir / f"{concern}.md"
        assert f.is_symlink(), f"reviewer-{concern} not flattened into ~/.claude/agents/"


# ---- skills ----------------------------------------------------------------

@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_skills_linked_on_standard_profile(fake_home):
    _run("--profile", "standard", fake_home=fake_home)
    skills_dir = fake_home / ".claude" / "skills"
    for name in ("tdd-loop", "worktree", "recall", "commit-discipline"):
        sd = skills_dir / name
        assert sd.is_symlink(), f"skill {name} not linked"
        # Resolves to a directory containing SKILL.md
        assert (sd / "SKILL.md").exists(), f"skill {name}/SKILL.md unreachable"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_minimal_profile_skips_skills(fake_home):
    """Minimal profile should be lean — no skills, no hooks. Commands/agents
    still install because the `/ucw` command is needed for any UCW workflow."""
    _run("--profile", "minimal", fake_home=fake_home)
    skills_dir = fake_home / ".claude" / "skills"
    # Either dir doesn't exist or it's empty (no UCW skills)
    if skills_dir.exists():
        ucw_skills = [d for d in skills_dir.iterdir() if d.is_symlink()]
        assert not ucw_skills, f"minimal should skip skills, found: {ucw_skills}"


# ---- uninstall preserves user files ----------------------------------------

@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_uninstall_preserves_user_commands(fake_home):
    """User-authored commands/agents/skills must survive --uninstall.
    Only the UCW-tagged symlinks should disappear."""
    # User has their own command BEFORE installing UCW
    user_cmd = fake_home / ".claude" / "commands" / "my-own-command.md"
    user_cmd.parent.mkdir(parents=True)
    user_cmd.write_text("---\nname: my-own\n---\nuser-authored")

    # User has their own agent
    user_agent = fake_home / ".claude" / "agents" / "my-helper.md"
    user_agent.parent.mkdir(parents=True)
    user_agent.write_text("---\nname: my-helper\ntools: [Read]\n---\nuser-authored")

    # User has their own skill
    user_skill = fake_home / ".claude" / "skills" / "my-skill" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("---\nname: my-skill\n---\nuser-authored")

    # Install UCW, then uninstall
    _run("--profile", "standard", fake_home=fake_home)
    _run("--uninstall", fake_home=fake_home)

    # User files must remain, UCW symlinks must be gone
    assert user_cmd.exists() and user_cmd.read_text() == "---\nname: my-own\n---\nuser-authored"
    assert user_agent.exists() and "user-authored" in user_agent.read_text()
    assert user_skill.exists() and "user-authored" in user_skill.read_text()
    # And UCW's own symlinks were cleaned up
    assert not (fake_home / ".claude" / "commands" / "ucw.md").exists()
    assert not (fake_home / ".claude" / "agents" / "planner.md").exists()
    assert not (fake_home / ".claude" / "skills" / "tdd-loop").exists()


# ---- idempotent ------------------------------------------------------------

@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_reinstall_does_not_duplicate(fake_home):
    """Running install twice should produce the same symlink set, not duplicates."""
    _run("--profile", "standard", fake_home=fake_home)
    before = sorted((fake_home / ".claude" / "agents").iterdir())
    _run("--profile", "standard", fake_home=fake_home)
    after = sorted((fake_home / ".claude" / "agents").iterdir())
    assert before == after


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_reinstall_does_not_create_circular_symlinks_in_source(fake_home):
    """Regression for a real bug: `ln -sf <src_dir> <dest>` on an existing
    symlink-to-dir dereferences the destination and writes the link INSIDE
    the source directory, creating a circular symlink. The fix is `ln -sfn`
    (replace the symlink itself).

    This test reinstalls 3 times and asserts the source tree has no stray
    symlinks under skills/.
    """
    for _ in range(3):
        _run("--profile", "standard", fake_home=fake_home)

    # No new symlinks should appear inside the repo's skills/ tree
    stray = [
        p for p in REPO_ROOT.glob("skills/**/*")
        if p.is_symlink() and p.name not in {".gitkeep"}
    ]
    assert not stray, (
        f"Reinstall created stray symlinks in the source tree: {stray}. "
        f"This means `ln -sf` is dereferencing dest dirs — use `ln -sfn`."
    )
