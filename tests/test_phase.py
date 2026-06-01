"""Tests for the phase-tracker helper."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_phase():
    spec = importlib.util.spec_from_file_location(
        "ucw_phase", REPO_ROOT / "bin" / "ucw-phase.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_phase"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_get_when_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()
    phase = _load_phase()
    rc = phase.main(["get"])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out)["phase"] is None


def test_set_then_get(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()
    phase = _load_phase()

    rc = phase.main(["set", "plan"])
    assert rc == 0
    capsys.readouterr()

    rc = phase.main(["get"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["phase"] == "plan"


def test_set_rejects_invalid(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()
    phase = _load_phase()
    # argparse choices=… will SystemExit on invalid values
    import pytest
    with pytest.raises(SystemExit):
        phase.main(["set", "lunch"])


def test_clear(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()
    phase = _load_phase()
    phase.main(["set", "build"])
    capsys.readouterr()
    rc = phase.main(["clear"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["phase"] is None
    assert body["cleared"] is True
