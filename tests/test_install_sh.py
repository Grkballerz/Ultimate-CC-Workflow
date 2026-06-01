"""Tests for install.sh — exercises dry-run, profiles, uninstall paths.

Each test isolates HOME so it doesn't touch the developer's real ~/.claude.
"""
from __future__ import annotations

import json
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
    env = os.environ.copy()
    cp = subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )
    return cp.returncode, cp.stdout, cp.stderr


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_help_exits_zero(fake_home):
    rc, out, _ = _run("--help", fake_home=fake_home)
    assert rc == 0
    assert "Usage" in out or "Profiles" in out


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_dry_run_does_not_create_files(fake_home):
    rc, _out, _ = _run("--profile", "standard", "--dry-run", fake_home=fake_home)
    assert rc == 0
    # Settings file should NOT exist after dry run
    assert not (fake_home / ".claude" / "settings.json").exists() \
        or (fake_home / ".claude" / "settings.json").read_text().strip() == "{}"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_minimal_profile_install(fake_home):
    rc, out, err = _run("--profile", "minimal", fake_home=fake_home)
    assert rc == 0, f"install failed:\n{out}\n{err}"
    # Rules should be copied
    assert (fake_home / ".claude" / "rules" / "ucw" / "core.md").exists()
    # bin should be linked
    assert (fake_home / ".claude" / "ucw" / "bin" / "ucw-audit.py").is_symlink() \
        or (fake_home / ".claude" / "ucw" / "bin" / "ucw-audit.py").exists()
    # Settings should have something merged
    settings = fake_home / ".claude" / "settings.json"
    assert settings.exists()
    body = json.loads(settings.read_text())
    assert body.get("_profile") == "ucw-minimal"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_standard_profile_installs_hooks(fake_home):
    rc, _, _ = _run("--profile", "standard", fake_home=fake_home)
    assert rc == 0
    hooks_dir = fake_home / ".claude" / "ucw" / "hooks"
    assert (hooks_dir / "session-start.py").exists()
    assert (hooks_dir / "post-tool-use.py").exists()
    # MCP server registered
    mcp = json.loads((fake_home / ".claude" / "mcp.json").read_text())
    assert "ucw-memory" in mcp["mcpServers"]


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_idempotent_reinstall(fake_home):
    _run("--profile", "minimal", fake_home=fake_home)
    settings_before = (fake_home / ".claude" / "settings.json").read_text()
    rc, _, _ = _run("--profile", "minimal", fake_home=fake_home)
    assert rc == 0
    settings_after = (fake_home / ".claude" / "settings.json").read_text()
    # Idempotent: re-running should produce the same merged shape
    assert json.loads(settings_before) == json.loads(settings_after)


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_uninstall_removes_directories(fake_home):
    _run("--profile", "standard", fake_home=fake_home)
    assert (fake_home / ".claude" / "rules" / "ucw").exists()

    rc, _, _ = _run("--uninstall", fake_home=fake_home)
    assert rc == 0
    assert not (fake_home / ".claude" / "rules" / "ucw").exists()
    # MCP entry should be gone
    if (fake_home / ".claude" / "mcp.json").exists():
        mcp = json.loads((fake_home / ".claude" / "mcp.json").read_text())
        assert "ucw-memory" not in mcp.get("mcpServers", {})


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_post_marketplace_lays_down_rules(fake_home):
    rc, _, _ = _run("--post-marketplace", "--profile", "standard", fake_home=fake_home)
    assert rc == 0
    assert (fake_home / ".claude" / "rules" / "ucw" / "core.md").exists()


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_unknown_profile_rejected(fake_home):
    rc, _, _err = _run("--profile", "bogus", fake_home=fake_home)
    assert rc != 0
