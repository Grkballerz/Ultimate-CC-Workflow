"""Regression tests for the PEP 668 venv fix.

The original install.sh used `pip install -e ./memory` against the system
Python, which fails on Debian/Ubuntu/Zorin/Homebrew-3.11+ (PEP 668). The
fallback was "set PYTHONPATH" — but that doesn't help when Claude Code
launches the MCP server via the registered `command` and the python at
that path can't import ucw_memory.

These tests lock in the corrected behavior:

  1. install_memory_deps creates $UCW_HOME/venv (when uv is available
     or when PEP 668 is detected, but uv works in this CI env)
  2. The venv's python can `import ucw_memory`
  3. mcp.json's `command` field is the ABSOLUTE PATH to that python,
     not "python3"
  4. --reinstall-deps rebuilds the venv
  5. --uninstall removes the venv
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
    cp = subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True, text=True, env=os.environ.copy(), timeout=180,
    )
    return cp.returncode, cp.stdout, cp.stderr


def _venv_python(fake_home: Path) -> Path:
    return fake_home / ".claude" / "ucw" / "venv" / "bin" / "python"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_install_creates_venv_with_ucw_memory(fake_home):
    rc, out, err = _run("--profile", "minimal", fake_home=fake_home)
    assert rc == 0, f"install failed:\n{out}\n{err}"

    py = _venv_python(fake_home)
    assert py.exists(), f"venv python missing at {py}"

    # The venv's python must be able to import ucw_memory (the whole point)
    probe = subprocess.run(
        [str(py), "-c", "import ucw_memory; print(ucw_memory.__version__)"],
        capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, \
        f"venv python cannot import ucw_memory:\n  stdout: {probe.stdout}\n  stderr: {probe.stderr}"
    assert probe.stdout.strip()  # version string non-empty


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_mcp_json_command_points_at_venv_python(fake_home):
    rc, _, _ = _run("--profile", "minimal", fake_home=fake_home)
    assert rc == 0
    mcp = json.loads((fake_home / ".claude" / "mcp.json").read_text())
    cmd = mcp["mcpServers"]["ucw-memory"]["command"]
    expected = str(_venv_python(fake_home))
    assert cmd == expected, (
        f"mcp.json command is {cmd!r}; expected absolute path to the venv python "
        f"({expected!r}). Using bare 'python3' would break on systems where the "
        f"system python is externally-managed and ucw_memory only lives in the venv."
    )


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_mcp_json_env_sets_ucw_memory_home(fake_home):
    _run("--profile", "minimal", fake_home=fake_home)
    mcp = json.loads((fake_home / ".claude" / "mcp.json").read_text())
    env = mcp["mcpServers"]["ucw-memory"]["env"]
    assert env["UCW_MEMORY_HOME"] == str(fake_home / ".claude" / "ucw")


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_reinstall_deps_rebuilds_venv(fake_home):
    """uv's python binary mtime is content-addressed (won't change on rebuild),
    so we check pyvenv.cfg — it's rewritten every time the venv is created.
    """
    _run("--profile", "minimal", fake_home=fake_home)
    cfg = fake_home / ".claude" / "ucw" / "venv" / "pyvenv.cfg"
    assert cfg.exists()
    original_inode = cfg.stat().st_ino
    original_mtime = cfg.stat().st_mtime

    # Re-run without --reinstall-deps: venv should be reused, pyvenv.cfg untouched
    _run("--profile", "minimal", fake_home=fake_home)
    assert cfg.stat().st_ino == original_inode, "venv was rebuilt without --reinstall-deps"

    # Now force a rebuild
    import time
    time.sleep(1.1)  # filesystem mtime granularity
    rc, _, _ = _run("--profile", "minimal", "--reinstall-deps", fake_home=fake_home)
    assert rc == 0
    assert cfg.exists()
    # Either a new inode (rm -rf then recreate) or a fresher mtime
    rebuilt = (cfg.stat().st_ino != original_inode) or (cfg.stat().st_mtime > original_mtime)
    assert rebuilt, "--reinstall-deps did not rebuild the venv"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_uninstall_removes_venv(fake_home):
    _run("--profile", "minimal", fake_home=fake_home)
    venv = fake_home / ".claude" / "ucw" / "venv"
    assert venv.exists()

    rc, _, _ = _run("--uninstall", fake_home=fake_home)
    assert rc == 0
    assert not venv.exists(), "venv survived uninstall"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_dry_run_does_not_create_venv(fake_home):
    rc, _, _ = _run("--profile", "minimal", "--dry-run", fake_home=fake_home)
    assert rc == 0
    venv = fake_home / ".claude" / "ucw" / "venv"
    assert not venv.exists(), "--dry-run should not create the venv"


@pytest.mark.skipif(not _has("jq"), reason="jq required by installer")
def test_idempotent_reinstall_keeps_venv_command_correct(fake_home):
    """Running install twice in a row should leave mcp.json with the same
    absolute venv command path — no drift back to bare 'python3'."""
    _run("--profile", "minimal", fake_home=fake_home)
    mcp_first = json.loads((fake_home / ".claude" / "mcp.json").read_text())

    _run("--profile", "minimal", fake_home=fake_home)
    mcp_second = json.loads((fake_home / ".claude" / "mcp.json").read_text())

    assert mcp_first["mcpServers"]["ucw-memory"] == mcp_second["mcpServers"]["ucw-memory"]
    assert "venv/bin/python" in mcp_second["mcpServers"]["ucw-memory"]["command"]
