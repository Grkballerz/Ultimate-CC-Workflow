"""Forbid commands/*.md files whose names collide with Claude Code built-ins.

Claude Code reserves a set of slash commands (`/plan`, `/init`, `/review`,
`/help`, `/clear`, `/compact`, `/config`, `/model`, `/cost`, `/mcp`, `/agents`,
`/plugin`, `/permissions`, `/status`, `/login`, `/logout`, `/bug`, `/release-notes`,
`/exit`, `/quit`). A plugin file with one of those stems either gets shadowed
silently or behaves differently depending on the install path.

UCW's policy: every UCW command is a SUBCOMMAND of `/ucw`. There is exactly
one top-level command file (`commands/ucw.md`). This test enforces that.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Claude Code's documented built-in slash commands. Source:
# https://code.claude.com/docs (commands reference)
RESERVED_BUILTINS: set[str] = {
    # workflow / mode
    "plan", "init", "review",
    # session
    "help", "clear", "compact", "cost", "status",
    # config
    "config", "model", "mcp", "agents", "plugin", "permissions",
    # meta
    "bug", "release-notes", "login", "logout", "exit", "quit",
}

# Names we know are safe and intend to ship as top-level commands. New
# additions need an explicit entry here — otherwise the catch-all below
# rejects them.
ALLOWED_TOP_LEVEL: set[str] = {
    "ucw",  # the single root that everything dispatches through
}


def _command_stems() -> set[str]:
    return {p.stem for p in (REPO_ROOT / "commands").glob("*.md")}


def test_no_command_collides_with_claude_builtin():
    stems = _command_stems()
    collisions = stems & RESERVED_BUILTINS
    assert not collisions, (
        f"commands/{{{','.join(sorted(collisions))}}}.md collide with Claude Code "
        f"built-ins. Move their behavior into commands/ucw.md as a subcommand "
        f"and delete the standalone file."
    )


def test_only_ucw_is_top_level():
    """Every command file other than the umbrella must be on the allowlist.

    This is the durable rule: net-new top-level commands need a deliberate
    decision (does it warrant a top-level root? if so, add it to ALLOWED_TOP_LEVEL
    in this test). Most additions should be subcommands of `/ucw`.
    """
    stems = _command_stems()
    unexpected = stems - ALLOWED_TOP_LEVEL
    assert not unexpected, (
        f"Unexpected top-level commands: {sorted(unexpected)}. "
        f"UCW policy: everything goes under `/ucw <subcommand>`. "
        f"If a new top-level root is truly needed, add it to "
        f"ALLOWED_TOP_LEVEL in this test with reasoning."
    )


def test_ucw_dispatcher_exists():
    """The umbrella file must exist — every UCW command routes through it."""
    assert (REPO_ROOT / "commands" / "ucw.md").exists()


# NOTE: the old test_ucw_dispatcher_documents_each_subcommand lived here with
# a hardcoded 15-item subcommand tuple that went stale (missed prefs, ask,
# opinion, auto, settings, resume). It is superseded by
# tests/test_dispatch_drift.py, which PARSES the dispatch table from
# commands/ucw.md and cross-checks sections, bin/ scripts, and README.md.
