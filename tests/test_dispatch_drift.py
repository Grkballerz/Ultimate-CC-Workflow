"""Drift guards between commands/ucw.md, bin/, and README.md.

The dispatch table at the top of commands/ucw.md is the single source of
truth for what `/ucw` can do. Instead of hardcoding a subcommand list that
goes stale (the old 15-item tuple missed prefs/ask/opinion/auto/settings/
resume), these tests PARSE the table and cross-check three surfaces:

- every table entry has a matching `## <sub>` section in ucw.md
- every `bin/ucw-*.py` referenced anywhere in ucw.md actually exists
- every `bin/ucw-*.py` shipped in the repo is mentioned somewhere in ucw.md
  (a helper nobody can discover is a helper nobody runs)
- README.md mentions every dispatch-table subcommand as `/ucw <sub>`

No network, no subprocesses — pure file parsing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UCW_MD = REPO_ROOT / "commands" / "ucw.md"
README = REPO_ROOT / "README.md"
BIN_DIR = REPO_ROOT / "bin"


def _ucw_text() -> str:
    return UCW_MD.read_text(encoding="utf-8")


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def dispatch_table_entries() -> list[str]:
    """Subcommand tokens parsed from the fenced dispatch table.

    The table is the first fenced block, opening with `ucw <sub> [args...]`;
    each entry line is two-space-indented with the token first.
    """
    m = re.search(r"```\n(ucw <sub>.*?)```", _ucw_text(), re.DOTALL)
    assert m, "commands/ucw.md must contain the fenced dispatch table"
    entries: list[str] = []
    for line in m.group(1).splitlines()[1:]:
        tok = re.match(r"^ {2}(\S+)\s", line)
        if tok:
            entries.append(tok.group(1))
    return entries


def referenced_bins() -> list[str]:
    """Every bin/ucw-*.py filename mentioned anywhere in ucw.md."""
    return sorted(set(re.findall(r"\bucw-[\w-]+\.py\b", _ucw_text())))


def shipped_bins() -> list[str]:
    return sorted(p.name for p in BIN_DIR.glob("ucw-*.py"))


def test_dispatch_table_parses_and_is_not_trivial():
    entries = dispatch_table_entries()
    assert len(entries) >= 15, f"suspiciously small dispatch table: {entries}"
    # Spot-check entries the README/docs have promised for a while.
    for expected in ("status", "review", "ship", "settings", "opinion", "auto"):
        assert expected in entries, f"dispatch table lost `{expected}`"


@pytest.mark.parametrize("sub", dispatch_table_entries())
def test_every_table_entry_has_a_section(sub: str):
    assert re.search(rf"^## {re.escape(sub)}\b", _ucw_text(), re.MULTILINE), \
        f"dispatch table lists `{sub}` but commands/ucw.md has no `## {sub}` section"


@pytest.mark.parametrize("name", referenced_bins())
def test_every_referenced_bin_exists(name: str):
    assert (BIN_DIR / name).exists(), \
        f"commands/ucw.md references bin/{name} which does not exist"


@pytest.mark.parametrize("name", shipped_bins())
def test_every_shipped_bin_is_mentioned(name: str):
    assert name in _ucw_text(), (
        f"bin/{name} is shipped but never mentioned in commands/ucw.md — "
        f"wire it into a subcommand section (or note why it isn't dispatched, "
        f"as done for installer plumbing)"
    )


@pytest.mark.parametrize("sub", dispatch_table_entries())
def test_readme_mentions_every_subcommand(sub: str):
    assert f"/ucw {sub}" in _readme_text(), \
        f"README.md slash-command table is missing `/ucw {sub}`"


def test_readme_mentions_kimi_opt_in_flags():
    """The Kimi lanes are opt-in; the README must at least name the switches."""
    text = _readme_text()
    for token in ("--with-kimi", "--disprover-model", "[kimi]"):
        assert token in text, f"README.md does not mention {token!r}"
