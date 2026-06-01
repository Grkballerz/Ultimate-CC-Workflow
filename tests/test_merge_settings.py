"""Tests for bin/ucw-merge-settings.py — the install.sh settings merger.

Critical behaviors to lock down:
- User-authored hooks NEVER get dropped on install
- Reinstall is idempotent (no duplicate UCW entries)
- Uninstall strips UCW entries but keeps user entries
- Mixed UCW + user under the same event preserves both
- Emptied event arrays disappear from settings.hooks
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_merge_settings", REPO_ROOT / "bin" / "ucw-merge-settings.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_merge_settings"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---- direct function tests ---------------------------------------------------

def test_strip_keeps_user_hooks_when_no_ucw_present():
    m = _load()
    data = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user"}]}
            ]
        }
    }
    out = m.strip_ucw_entries(data)
    assert out == data


def test_strip_drops_ucw_keeps_user_in_same_event():
    m = _load()
    data = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user"}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "$HOME/.claude/ucw/hooks/pre.py"}]},
            ]
        }
    }
    out = m.strip_ucw_entries(data)
    assert len(out["hooks"]["PreToolUse"]) == 1
    assert "echo user" in out["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_strip_removes_empty_event_keys():
    m = _load()
    data = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "ucw"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "ucw-stop"}]}],
        }
    }
    out = m.strip_ucw_entries(data)
    # All UCW → both events should be gone
    assert out["hooks"] == {}


def test_merge_preserves_user_marker_and_other_keys():
    m = _load()
    base = {"model": "sonnet", "_marker": "keep me", "hooks": {}}
    frag = {"_profile": "ucw-standard"}
    out = m.merge(base, frag)
    assert out["model"] == "sonnet"
    assert out["_marker"] == "keep me"
    assert out["_profile"] == "ucw-standard"


def test_merge_concatenates_arrays_not_replaces():
    m = _load()
    base = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "user1"}]}
    ]}}
    frag = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "$HOME/.claude/ucw/hooks/x.py"}]}
    ]}}
    out = m.merge(base, frag)
    cmds = [h["command"] for b in out["hooks"]["PreToolUse"] for h in b["hooks"]]
    assert "user1" in cmds
    assert any("ucw" in c for c in cmds)


def test_merge_is_idempotent_for_ucw_entries():
    m = _load()
    base = {"hooks": {}}
    frag = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "$HOME/.claude/ucw/hooks/x.py"}]}
    ]}}
    first = m.merge(base, frag)
    second = m.merge(first, frag)
    third = m.merge(second, frag)
    # All three should be identical — no duplicates accumulate
    assert first == second == third


def test_merge_idempotent_mixed_user_plus_ucw():
    """Three installs over a base that already has a user hook → user kept,
    ucw single."""
    m = _load()
    user_base = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user"}]}
    ]}}
    frag = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "$HOME/.claude/ucw/hooks/x.py"}]}
    ]}}
    a = m.merge(user_base, frag)
    b = m.merge(a, frag)
    c = m.merge(b, frag)
    assert a == b == c
    pre = c["hooks"]["PreToolUse"]
    cmds = [h["command"] for blk in pre for h in blk["hooks"]]
    assert sum(1 for x in cmds if "user" in x) == 1
    assert sum(1 for x in cmds if "ucw" in x) == 1


# ---- CLI tests ---------------------------------------------------------------

def test_cli_writes_to_stdout(tmp_path, capsys):
    m = _load()
    base = tmp_path / "base.json"
    frag = tmp_path / "frag.json"
    base.write_text('{"a": 1}')
    frag.write_text('{"b": 2}')
    rc = m.main([str(base), str(frag)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"a": 1, "b": 2}


def test_cli_inplace_overwrites(tmp_path):
    m = _load()
    base = tmp_path / "base.json"
    frag = tmp_path / "frag.json"
    base.write_text('{"a": 1}')
    frag.write_text('{"b": 2}')
    rc = m.main(["--inplace", str(base), str(frag)])
    assert rc == 0
    assert json.loads(base.read_text()) == {"a": 1, "b": 2}


def test_cli_uninstall_strips_ucw(tmp_path, capsys):
    m = _load()
    base = tmp_path / "base.json"
    base.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user"}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "$HOME/.claude/ucw/hooks/x.py"}]},
            ]
        }
    }))
    rc = m.main(["--uninstall", str(base)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(out["hooks"]["PreToolUse"]) == 1
    assert "echo user" in out["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_cli_missing_base_file_treated_as_empty(tmp_path):
    m = _load()
    frag = tmp_path / "frag.json"
    frag.write_text('{"a": 1}')
    rc = m.main([str(tmp_path / "does-not-exist.json"), str(frag), "--output", str(tmp_path / "out.json")])
    assert rc == 0
    assert json.loads((tmp_path / "out.json").read_text()) == {"a": 1}


def test_cli_uninstall_without_fragment_works(tmp_path, capsys):
    m = _load()
    base = tmp_path / "base.json"
    base.write_text('{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "ucw-stop"}]}]}}')
    rc = m.main(["--uninstall", str(base)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    # Stop event was UCW-only → entire event removed
    assert out["hooks"] == {}
