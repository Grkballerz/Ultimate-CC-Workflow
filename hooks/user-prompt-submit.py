#!/usr/bin/env python3
"""UserPromptSubmit hook — inject relevant Knowledge docs based on prompt content
plus a resume hint when UCW workflow state is set.

Two injection channels into `additionalContext`:

1. **Knowledge** (existing): cheap keyword match → `.ucw/knowledge/*.md`.
2. **Resume hint** (PR D): if `.ucw/state/phase` exists, prepend a small
   block naming the phase, auto-mode level, and pointers to plan.md / spec.md
   so the agent re-orients after `/clear` or `/compact` without manual
   `/ucw status` + `cat`.

Both fit in `hookSpecificOutput.additionalContext` which IS valid for
UserPromptSubmit per Claude Code's strict hook schema.
"""
from __future__ import annotations

import re
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
