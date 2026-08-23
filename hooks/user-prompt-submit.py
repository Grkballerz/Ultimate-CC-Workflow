#!/usr/bin/env python3
"""UserPromptSubmit hook — inject relevant Knowledge docs based on prompt content
plus a resume hint when UCW workflow state is set.

Three injection channels into `additionalContext`:

1. **Knowledge** (existing): cheap keyword match → `.ucw/knowledge/*.md`.
2. **Resume hint** (PR D): if `.ucw/state/phase` exists, prepend a small
   block naming the phase, auto-mode level, and pointers to plan.md / spec.md
   so the agent re-orients after `/clear` or `/compact` without manual
   `/ucw status` + `cat`.
3. **Pinned facts** (QW4): `memory.pin` promises "always in SessionStart",
   but SessionStart can't inject context (strict schema rejects it) — so the
   first prompt of each session carries a compact, budget-capped pinned-facts
   block instead. Session detection reuses the `last-session-start` marker
   written by hooks/session-start.py.

All fit in `hookSpecificOutput.additionalContext` which IS valid for
UserPromptSubmit per Claude Code's strict hook schema.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import (
    auto_mode_level,
    auto_retry_cap,
    log,
    project_root,
    read_payload,
    state_file,
    write_output,
)

KEYWORD_TO_DOC: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(stack|dependenc|version|install|package|library|framework)\b", re.I),         "STACK.md"),
    (re.compile(r"\b(architecture|design|module|abstraction|why\s+did|why\s+do)\b", re.I),         "DESIGN.md"),
    (re.compile(r"\b(convention|style|naming|lint|format|test\s+pattern)\b", re.I),                "CONVENTIONS.md"),
    (re.compile(r"\b(roadmap|plan|todo|backlog|wip)\b", re.I),                                     "ROADMAP.md"),
    (re.compile(r"\b(term|glossary|what\s+is\s+a)\b", re.I),                                       "GLOSSARY.md"),
    (re.compile(r"\b(image|video|svg|audio|diagram|render|generate)\b", re.I),                     "PREFERENCES.md"),
]


def _knowledge_dir(payload: dict) -> Path:
    return project_root(payload) / ".ucw" / "knowledge"


# ---- pinned-facts block (QW4) ----------------------------------------------

_PINNED_BUDGET = 600
_PINNED_MARKER = "pinned-injected"


def _memory_db_path(payload: dict) -> Path:
    return project_root(payload) / ".ucw" / "memory.sqlite"


def _session_token(payload: dict) -> str:
    """Identity of the current session: payload session_id, else the content
    of the `last-session-start` marker session-start.py writes."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    try:
        return state_file(payload, "last-session-start").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_first_prompt_of_session(payload: dict) -> bool:
    """True on the first prompt of a session; refreshes the marker.

    Compares the session token against `.ucw/state/pinned-injected` from the
    last injection. With no token at all (no session_id, no session-start
    marker), fall back to mtime: inject when our marker is missing or older
    than `last-session-start`, then refresh it.
    """
    marker = state_file(payload, _PINNED_MARKER)
    token = _session_token(payload)
    if token:
        try:
            if marker.exists() and marker.read_text(encoding="utf-8").strip() == token:
                return False
        except OSError:
            pass
    else:
        lss = state_file(payload, "last-session-start")
        try:
            if marker.exists() and (
                not lss.exists() or marker.stat().st_mtime >= lss.stat().st_mtime
            ):
                return False
        except OSError:
            return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(token, encoding="utf-8")
    except OSError:
        pass
    return True


def _pinned_fact_lines(db_path: Path, limit: int = 20) -> list[str]:
    """Read pinned facts (most recent first) straight from the memory DB.

    Direct sqlite3 keeps the hook self-contained — no ucw_memory import."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                """SELECT subject, predicate, object, reason FROM facts
                    WHERE deleted = 0 AND pinned = 1
                    ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [f"- {s} {p} {o} (because {r})" for s, p, o, r in rows]


def _build_pinned_block(payload: dict) -> str | None:
    """Compact pinned-facts block, injected on the FIRST prompt of a session
    only, and only when the memory DB exists. Budget-capped at ~600 chars,
    most recent pins first."""
    db_path = _memory_db_path(payload)
    if not db_path.exists():
        return None
    if not _is_first_prompt_of_session(payload):
        return None
    lines = _pinned_fact_lines(db_path)
    if not lines:
        return None
    header = "## UCW Memory (pinned)"
    out = [header]
    used = len(header)
    for line in lines:
        if used + len(line) + 1 > _PINNED_BUDGET:
            break
        out.append(line)
        used += len(line) + 1
    if len(out) == 1:
        return None
    return "\n".join(out)


def _build_resume_hint(payload: dict) -> str | None:
    """Build a 150-300 char hint if UCW workflow state is set, else None.

    Designed to be cheap to ignore: agent that doesn't need it loses a few
    tokens; agent that just lost context after /clear gets oriented.
    """
    phase_sf = state_file(payload, "phase")
    if not phase_sf.exists():
        return None
    try:
        phase = phase_sf.read_text().strip()
    except OSError:
        return None
    if not phase:
        return None

    level = auto_mode_level(payload)
    parts = [f"phase=`{phase}`"]
    if level:
        cap = auto_retry_cap(payload) if level >= 2 else None
        retries_sf = state_file(payload, "auto-retries")
        retries = 0
        if retries_sf.exists():
            try:
                retries = int(retries_sf.read_text().strip() or "0")
            except (OSError, ValueError):
                pass
        parts.append(
            f"auto-mode=L{level}"
            + (f" (retries {retries}/{cap})" if cap else "")
        )

    pointers = []
    if state_file(payload, "plan.md").exists():
        pointers.append("`.ucw/state/plan.md`")
    if state_file(payload, "spec.md").exists():
        pointers.append("`.ucw/state/spec.md`")
    pointer_line = (
        f" Plan/spec at {', '.join(pointers)}." if pointers else ""
    )

    return (
        "## UCW resume\n\n"
        f"Workflow state in progress: {', '.join(parts)}.{pointer_line} "
        "Run `/ucw resume` for the full block, or `/ucw status` for the dashboard view."
    )


def main() -> int:
    payload = read_payload()
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0

    sections: list[str] = []

    # Resume hint goes FIRST — small, always-relevant when workflow is mid-flight.
    resume_hint = _build_resume_hint(payload)
    if resume_hint:
        sections.append(resume_hint)

    # Pinned facts on the first prompt of a session (QW4 delivery channel).
    pinned_block = _build_pinned_block(payload)
    if pinned_block:
        sections.append(pinned_block)
        log(payload, "injected pinned memory block (first prompt of session)")

    docs_dir = _knowledge_dir(payload)
    if docs_dir.is_dir():
        matched: list[str] = []
        for pattern, doc in KEYWORD_TO_DOC:
            if pattern.search(prompt) and (docs_dir / doc).exists() and doc not in matched:
                matched.append(doc)

        # Cap total injection size to keep token budget under control.
        budget = 4000
        used = sum(len(s) for s in sections)
        for doc in matched:
            try:
                content = (docs_dir / doc).read_text(encoding="utf-8")
            except OSError:
                continue
            slice_ = content[: max(0, budget - used)]
            if not slice_:
                break
            sections.append(f"## `{doc}` (UCW Knowledge)\n\n{slice_}")
            used += len(slice_)

        if matched:
            log(payload, f"injected knowledge docs: {matched}")

    if not sections:
        return 0

    if resume_hint:
        log(payload, "injected resume hint (phase state present)")

    write_output({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n---\n\n".join(sections),
        }
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
