"""Doc contract for the `/ucw settings` subcommand.

The section in commands/ucw.md must reproduce the FULL key registry from
bin/ucw-settings.py — the registry is parsed from the module itself and
cross-checked against the doc, so adding a key without documenting it
fails here. No network, no subprocesses.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UCW_MD = REPO_ROOT / "commands" / "ucw.md"


def _load_settings():
    spec = importlib.util.spec_from_file_location(
        "ucw_settings", REPO_ROOT / "bin" / "ucw-settings.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_settings"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _text() -> str:
    return UCW_MD.read_text(encoding="utf-8")


def _settings_section() -> str:
    """Body of `## settings ...` up to the next top-level heading."""
    lines = _text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^## settings\b", line):
            start = i
            break
    assert start is not None, "commands/ucw.md has no '## settings' section"
    body: list[str] = []
    in_fence = False
    for line in lines[start + 1:]:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


# ---- dispatch table ----------------------------------------------------------

def test_settings_appears_in_dispatch_table():
    assert re.search(r"^\s*settings\b", _text(), re.MULTILINE), \
        "the dispatch table must list the `settings` subcommand"


def test_settings_section_forwards_to_the_bin_script():
    assert "ucw-settings.py" in _settings_section()


def test_settings_section_documents_all_four_verbs():
    section = _settings_section()
    for verb in ("list", "get", "set", "unset"):
        assert verb in section, f"settings section missing verb: {verb}"


# ---- registry cross-check ----------------------------------------------------

def test_every_registry_key_is_documented():
    """Parse the registry from the module — no hardcoded key list here."""
    mod = _load_settings()
    section = _settings_section()
    assert mod.REGISTRY, "registry unexpectedly empty"
    for key in mod.REGISTRY:
        assert key in section, \
            f"commands/ucw.md settings section does not document {key!r}"


def test_documented_defaults_match_registry():
    """Each key's table row must state its actual registry default."""
    mod = _load_settings()
    section = _settings_section()
    for key, spec in mod.REGISTRY.items():
        row = next((ln for ln in section.splitlines()
                    if key in ln and ln.lstrip().startswith("|")), None)
        assert row is not None, f"no table row for {key!r}"
        default = str(spec.default).lower() if isinstance(spec.default, bool) \
            else str(spec.default)
        assert default in row, \
            f"table row for {key!r} does not show its default {default!r}: {row!r}"


def test_every_registry_key_has_a_wired_consumer():
    """Every key must be *consulted* somewhere — no inert settings.

    Pragmatic check: either the key's own table row names its consumer
    via the word 'consult', or the key name appears again outside the
    settings section (i.e. in the section that consumes it).
    """
    mod = _load_settings()
    section = _settings_section()
    outside = _text().replace(section, "")
    for key in mod.REGISTRY:
        row = next((ln for ln in section.splitlines()
                    if key in ln and ln.lstrip().startswith("|")), None)
        row_names_consumer = row is not None and "consult" in row.lower()
        consumed_elsewhere = key in outside
        assert row_names_consumer or consumed_elsewhere, (
            f"{key!r} has no consumer wiring in commands/ucw.md — either "
            f"mention it in its consuming section or say 'consulted by ...' "
            f"in its settings table row"
        )


def test_kimi_timeout_secs_documented_as_300():
    """The shared-contract default (300s) must be in both registry and doc."""
    mod = _load_settings()
    assert mod.REGISTRY["kimi.timeout_secs"].default == 300, \
        "registry default for kimi.timeout_secs changed — update the doc AND this test"
    section = _settings_section()
    row = next(ln for ln in section.splitlines()
               if "kimi.timeout_secs" in ln and ln.lstrip().startswith("|"))
    assert "300" in row, \
        f"kimi.timeout_secs table row must show default 300: {row!r}"


def test_kimi_transport_documented_as_default_auto_with_all_choices():
    """The transport chain (auto > claude-kimi > kimi-cli) must be in both
    registry and doc — default auto, every choice named in the table row."""
    mod = _load_settings()
    spec = mod.REGISTRY["kimi.transport"]
    assert spec.default == "auto", \
        "registry default for kimi.transport changed — update the doc AND this test"
    assert set(spec.choices or ()) == {"auto", "claude-kimi", "kimi-cli"}, \
        "kimi.transport choices changed — update the doc AND this test"
    section = _settings_section()
    row = next(ln for ln in section.splitlines()
               if "kimi.transport" in ln and ln.lstrip().startswith("|"))
    for choice in spec.choices:
        assert choice in row, \
            f"kimi.transport table row must list choice {choice!r}: {row!r}"
    assert "auto" in row, \
        f"kimi.transport table row must show default auto: {row!r}"


def test_kimi_transport_row_names_tool_less_fallback_constraint():
    """The row must state the fallback is restricted to tool-less calls —
    that constraint is the safety contract that keeps tool-scoped calls
    (the [kimi] implementer offload) from silently switching transports."""
    section = _settings_section()
    row = next(ln for ln in section.splitlines()
               if "kimi.transport" in ln and ln.lstrip().startswith("|"))
    assert "tool-less" in row.lower(), \
        f"kimi.transport row must name the tool-less-only fallback: {row!r}"


def test_kimi_offload_documented_as_default_off():
    """The offload kill-switch must be visibly default-off in the doc."""
    mod = _load_settings()
    assert mod.REGISTRY["kimi.offload"].default is False, \
        "registry default for kimi.offload changed — update the doc AND this test"
    section = _settings_section()
    row = next(ln for ln in section.splitlines() if "kimi.offload" in ln)
    assert "false" in row.lower(), \
        "kimi.offload must be documented with default false (opt-in offload)"


# ---- precedence + no-secrets framing -----------------------------------------

def test_settings_section_documents_precedence():
    section = _settings_section()
    # env override naming...
    assert "UCW_" in section
    # ...then project file, then default — in that order
    env_pos = section.find("UCW_")
    proj_pos = section.find(".ucw/state/settings.json", env_pos)
    default_pos = section.lower().find("registry default", proj_pos)
    assert env_pos != -1 and proj_pos != -1 and default_pos != -1, \
        "precedence chain (env > project settings.json > default) not documented"


def test_settings_section_says_never_store_secrets():
    section = _settings_section().lower()
    assert "secret" in section and ("never" in section or "no secret" in section), \
        "settings section must state that settings never store secrets"
