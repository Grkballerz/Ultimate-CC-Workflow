"""CLI tests for ucw-settings.py.

Everything runs against a throwaway project dir — no network, no real
claude-kimi, no shared state. Env overrides are exercised via monkeypatch.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_settings", REPO_ROOT / "bin" / "ucw-settings.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_settings"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Fresh project dir with a .ucw marker; scrub any UCW_* env leakage."""
    (tmp_path / ".ucw").mkdir()
    monkeypatch.chdir(tmp_path)
    mod = _load()
    for key in mod.REGISTRY:
        monkeypatch.delenv(mod._env_name(key), raising=False)
    return tmp_path


# ---- defaults ----------------------------------------------------------------

def test_list_reports_defaults_when_nothing_set(project, capsys):
    mod = _load()
    rc = mod.main(["list"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    rows = {r["key"]: r for r in body["settings"]}
    assert set(rows) == set(mod.REGISTRY)
    for key, row in rows.items():
        assert row["source"] == "default"
        assert row["value"] == mod.REGISTRY[key].default
        assert row["help"]  # every key carries one-line help


def test_get_default_values(project, capsys):
    mod = _load()
    for key, expected in [
        ("kimi.review", False),
        ("kimi.disprover", "haiku"),
        ("kimi.offload", False),
        ("kimi.model", "kimi-k3"),
        ("kimi.timeout_secs", 300),
        ("review.default", "full"),
        ("ship.push", True),
        ("ship.pr", False),
        ("scribe.auto", True),
        ("auto.default_level", 4),
        ("auto.retry_cap", 3),
    ]:
        rc = mod.main(["get", key])
        body = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert body == {"key": key, "value": expected, "source": "default"}


# ---- set / get / unset round-trip --------------------------------------------

def test_set_get_unset_round_trip(project, capsys):
    mod = _load()
    rc = mod.main(["set", "kimi.review", "true"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["value"] is True

    # backing store created at the state-dir convention path
    sp = project / ".ucw" / "state" / "settings.json"
    assert sp.exists()
    assert json.loads(sp.read_text())["kimi.review"] is True

    rc = mod.main(["get", "kimi.review"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body == {"key": "kimi.review", "value": True, "source": "project"}

    rc = mod.main(["unset", "kimi.review"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["cleared"] is True

    rc = mod.main(["get", "kimi.review"])
    body = json.loads(capsys.readouterr().out)
    assert body == {"key": "kimi.review", "value": False, "source": "default"}
    assert "kimi.review" not in json.loads(sp.read_text())


def test_set_int_and_enum_round_trip(project, capsys):
    mod = _load()
    mod.main(["set", "kimi.timeout_secs", "120"])
    capsys.readouterr()
    mod.main(["set", "kimi.disprover", "kimi"])
    capsys.readouterr()

    mod.main(["get", "kimi.timeout_secs"])
    assert json.loads(capsys.readouterr().out)["value"] == 120
    mod.main(["get", "kimi.disprover"])
    assert json.loads(capsys.readouterr().out)["value"] == "kimi"


def test_list_shows_project_source_after_set(project, capsys):
    mod = _load()
    mod.main(["set", "ship.pr", "yes"])
    capsys.readouterr()
    mod.main(["list"])
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)["settings"]}
    assert rows["ship.pr"]["value"] is True
    assert rows["ship.pr"]["source"] == "project"
    assert rows["ship.push"]["source"] == "default"  # untouched keys stay default


def test_unset_without_override_is_noop_success(project, capsys):
    mod = _load()
    rc = mod.main(["unset", "scribe.auto"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["cleared"] is False


# ---- kimi_invoke-consulted defaults ------------------------------------------

def test_kimi_timeout_secs_default_is_300(project, capsys):
    """kimi_invoke resolves its timeout from this key — the contract says 300."""
    mod = _load()
    assert mod.REGISTRY["kimi.timeout_secs"].default == 300
    rc = mod.main(["get", "kimi.timeout_secs"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body == {"key": "kimi.timeout_secs", "value": 300, "source": "default"}


# ---- auto-mode-consulted defaults --------------------------------------------

def test_auto_default_level_key_registered(project, capsys):
    """Bare `/ucw auto on` resolves its level from this key — default 4."""
    mod = _load()
    spec = mod.REGISTRY["auto.default_level"]
    assert spec.type == "int"
    assert spec.default == 4
    assert "auto on" in spec.help  # names the consumer, like the other keys
    rc = mod.main(["get", "auto.default_level"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body == {"key": "auto.default_level", "value": 4, "source": "default"}


def test_auto_retry_cap_key_registered(project, capsys):
    """The stop-hook retry loop resolves its cap from this key — default 3."""
    mod = _load()
    spec = mod.REGISTRY["auto.retry_cap"]
    assert spec.type == "int"
    assert spec.default == 3
    assert "retry" in spec.help
    rc = mod.main(["get", "auto.retry_cap"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body == {"key": "auto.retry_cap", "value": 3, "source": "default"}


def test_auto_keys_set_round_trip(project, capsys):
    mod = _load()
    mod.main(["set", "auto.default_level", "2"])
    capsys.readouterr()
    mod.main(["set", "auto.retry_cap", "5"])
    capsys.readouterr()

    mod.main(["get", "auto.default_level"])
    assert json.loads(capsys.readouterr().out)["value"] == 2
    mod.main(["get", "auto.retry_cap"])
    assert json.loads(capsys.readouterr().out)["value"] == 5


def test_auto_keys_env_names(project):
    """The env override names shared with ucw-auto / _hook_common."""
    mod = _load()
    assert mod._env_name("auto.default_level") == "UCW_AUTO_DEFAULT_LEVEL"
    assert mod._env_name("auto.retry_cap") == "UCW_AUTO_RETRY_CAP"


# ---- --repo flag -------------------------------------------------------------

def test_repo_flag_round_trip_in_other_dir(project, tmp_path_factory, capsys):
    """--repo pins the project root, overriding the cwd walk."""
    mod = _load()
    other = tmp_path_factory.mktemp("other-project")
    (other / ".ucw").mkdir()

    rc = mod.main(["set", "kimi.disprover", "kimi", "--repo", str(other)])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["value"] == "kimi"

    # backing store lands under --repo, not under cwd's project
    sp = other / ".ucw" / "state" / "settings.json"
    assert sp.exists()
    assert json.loads(sp.read_text())["kimi.disprover"] == "kimi"
    assert not (project / ".ucw" / "state" / "settings.json").exists()

    rc = mod.main(["get", "kimi.disprover", "--repo", str(other)])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body == {"key": "kimi.disprover", "value": "kimi", "source": "project"}

    # without --repo the cwd walk still governs — override is invisible here
    mod.main(["get", "kimi.disprover"])
    body = json.loads(capsys.readouterr().out)
    assert body == {"key": "kimi.disprover", "value": "haiku", "source": "default"}

    rc = mod.main(["unset", "kimi.disprover", "--repo", str(other)])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["cleared"] is True
    assert "kimi.disprover" not in json.loads(sp.read_text())


def test_repo_flag_list_reports_project_source(project, tmp_path_factory, capsys):
    mod = _load()
    other = tmp_path_factory.mktemp("other-project")
    (other / ".ucw").mkdir()
    mod.main(["set", "ship.pr", "yes", "--repo", str(other)])
    capsys.readouterr()

    mod.main(["list", "--repo", str(other)])
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)["settings"]}
    assert rows["ship.pr"]["value"] is True
    assert rows["ship.pr"]["source"] == "project"


# ---- validation --------------------------------------------------------------

def test_set_rejects_bad_bool(project, capsys):
    mod = _load()
    rc = mod.main(["set", "kimi.review", "maybe"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "kimi.review" in err


def test_set_rejects_bad_int(project, capsys):
    mod = _load()
    rc = mod.main(["set", "kimi.timeout_secs", "ninety"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "integer" in err


def test_set_rejects_bad_enum(project, capsys):
    mod = _load()
    rc = mod.main(["set", "kimi.disprover", "sonnet"])
    err = json.loads(capsys.readouterr().err)
    assert rc == 2
    assert "haiku" in err["error"] and "kimi" in err["error"]


def test_enum_spec_without_choices_raises_runtime_error(project):
    """The registry-bug guard is a real raise, not an assert (python -O safe)."""
    mod = _load()
    broken = mod.Spec("enum", "haiku", "malformed spec with no choices")
    with pytest.raises(RuntimeError):
        mod._parse_value(broken, "haiku")
    with pytest.raises(RuntimeError):
        mod._stored_value_valid(broken, "haiku")


def test_rejected_set_does_not_write_store(project, capsys):
    mod = _load()
    mod.main(["set", "kimi.review", "maybe"])
    capsys.readouterr()
    assert not (project / ".ucw" / "state" / "settings.json").exists()


# ---- unknown keys ------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["get", "kimi.bogus"],
    ["set", "kimi.bogus", "true"],
    ["unset", "kimi.bogus"],
])
def test_unknown_key_rejected_with_known_list(project, capsys, argv):
    mod = _load()
    rc = mod.main(argv)
    captured = capsys.readouterr()
    err = json.loads(captured.err)
    assert rc == 2
    assert "unknown key" in err["error"]
    assert err["known"] == sorted(mod.REGISTRY)


# ---- env-var overrides -------------------------------------------------------

def test_env_override_beats_file(project, capsys, monkeypatch):
    mod = _load()
    mod.main(["set", "kimi.disprover", "kimi"])
    capsys.readouterr()
    monkeypatch.setenv("UCW_KIMI_DISPROVER", "haiku")

    mod.main(["get", "kimi.disprover"])
    body = json.loads(capsys.readouterr().out)
    assert body == {"key": "kimi.disprover", "value": "haiku", "source": "env"}


def test_env_override_parses_bool_and_int(project, capsys, monkeypatch):
    mod = _load()
    monkeypatch.setenv("UCW_KIMI_OFFLOAD", "1")
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "30")

    mod.main(["get", "kimi.offload"])
    body = json.loads(capsys.readouterr().out)
    assert body["value"] is True
    assert body["source"] == "env"

    mod.main(["get", "kimi.timeout_secs"])
    body = json.loads(capsys.readouterr().out)
    assert body["value"] == 30
    assert body["source"] == "env"


def test_invalid_env_value_falls_through(project, capsys, monkeypatch):
    mod = _load()
    mod.main(["set", "kimi.timeout_secs", "45"])
    capsys.readouterr()
    monkeypatch.setenv("UCW_KIMI_TIMEOUT_SECS", "not-a-number")

    mod.main(["get", "kimi.timeout_secs"])
    body = json.loads(capsys.readouterr().out)
    assert body == {"key": "kimi.timeout_secs", "value": 45, "source": "project"}


def test_list_reports_env_source(project, capsys, monkeypatch):
    mod = _load()
    monkeypatch.setenv("UCW_SHIP_PUSH", "false")
    mod.main(["list"])
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)["settings"]}
    assert rows["ship.push"]["value"] is False
    assert rows["ship.push"]["source"] == "env"


# ---- resilience --------------------------------------------------------------

def test_corrupt_settings_file_falls_back_to_defaults(project, capsys):
    mod = _load()
    sp = project / ".ucw" / "state" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("{not json", encoding="utf-8")

    rc = mod.main(["get", "review.default"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body == {"key": "review.default", "value": "full", "source": "default"}


def test_wrong_typed_stored_value_ignored(project, capsys):
    """A hand-edited file with a mistyped value must not poison resolution."""
    mod = _load()
    sp = project / ".ucw" / "state" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"kimi.timeout_secs": "ninety"}), encoding="utf-8")

    mod.main(["get", "kimi.timeout_secs"])
    body = json.loads(capsys.readouterr().out)
    assert body == {"key": "kimi.timeout_secs", "value": 300, "source": "default"}
