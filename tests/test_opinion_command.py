"""Doc contract for the `/ucw opinion` subcommand.

`opinion` is an ad-hoc Kimi second opinion: advisory, unverified, and
strictly read-only. These tests pin the dispatch-table entry and the
section's read-only framing — no LLM calls, no subprocesses.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UCW_MD = REPO_ROOT / "commands" / "ucw.md"


def _text() -> str:
    return UCW_MD.read_text(encoding="utf-8")


def _section(name: str) -> str:
    """Body of `## <name>` up to the next top-level heading.

    Fence-aware: a `## ...` line inside a code fence (e.g. the sample
    output banner) does not terminate the section.
    """
    text = _text()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^## {re.escape(name)}\b", line):
            start = i
            break
    assert start is not None, f"commands/ucw.md has no '## {name}' section"
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

def test_opinion_appears_in_dispatch_table():
    text = _text()
    assert re.search(r"^\s*opinion\b", text, re.MULTILINE), \
        "the dispatch table must list the `opinion` subcommand"


def test_opinion_has_its_own_section():
    assert _section("opinion")  # raises if missing


# ---- advisory framing --------------------------------------------------------

def test_opinion_section_is_labelled_advisory():
    section = _section("opinion")
    assert "advisory" in section.lower(), \
        "the opinion section must frame Kimi's answer as advisory"


def test_opinion_section_references_the_bridge():
    section = _section("opinion")
    assert "kimi_invoke.py" in section or "claude-kimi" in section


# ---- primary path: prose via claude-kimi -p / kimi_invoke --raw ---------------

def test_opinion_primary_path_is_prose():
    """`claude-kimi -p` (or kimi_invoke.py --raw) is the PRIMARY documented
    path — prose answers are the normal case; JSON is only for structured use."""
    section = _section("opinion")
    assert "claude-kimi -p" in section, \
        "the opinion section must document claude-kimi -p for prose answers"
    assert "--raw" in section, \
        "the opinion section must document kimi_invoke.py --raw for prose answers"
    assert re.search(r"\bprimary\b", section, re.IGNORECASE), \
        "the opinion section must name the prose path as PRIMARY"


def test_opinion_documents_stdin_diff_pattern():
    """--diff pipes git diff into the bridge via the '-' stdin prompt marker."""
    section = _section("opinion")
    assert re.search(r"git diff \|.*kimi_invoke\.py - --raw", section), \
        "the opinion section must document `git diff | kimi_invoke.py - --raw`"


# ---- transport fallback note --------------------------------------------------

def test_opinion_documents_cli_fallback():
    """The bridge auto-falls-back to the standalone kimi CLI when the
    API-key path times out or hits quota — tool-less calls like opinion
    are exactly the ones eligible, so the section must say so."""
    section = _section("opinion")
    low = section.lower()
    assert re.search(r"falls?[ -]back|fallback", low), \
        "the opinion section must note the auto-fallback to the kimi CLI"
    assert "standalone" in low and "kimi" in low, \
        "the fallback note must name the standalone kimi CLI"
    assert "timeout" in low or "times out" in low, \
        "the fallback note must name the timeout trigger"
    assert "quota" in low, \
        "the fallback note must name the quota trigger"
    assert "kimi.transport" in section, \
        "the fallback note must point at the kimi.transport setting"


# ---- read-only: never instructs writes to .ucw/state -------------------------
# The section may (and should) MENTION .ucw/state — but only to negate it
# ("never touches .ucw/state"), mirroring the `ask` section's framing.

_NEGATION_MARKERS = ("never", "not ", "no ", "read-only")


def test_opinion_section_does_not_instruct_state_writes():
    section = _section("opinion")
    for i, line in enumerate(section.splitlines(), start=1):
        if ".ucw/state" not in line:
            continue
        low = line.lower()
        assert any(m in low for m in _NEGATION_MARKERS), (
            f"opinion section line {i} mentions .ucw/state outside a "
            f"negation — the opinion path must never write workflow state. "
            f"Line: {line.strip()!r}"
        )


def test_opinion_section_declares_itself_read_only():
    section = _section("opinion").lower()
    assert "read-only" in section
