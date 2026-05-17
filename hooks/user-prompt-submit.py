#!/usr/bin/env python3
"""UserPromptSubmit hook — inject relevant Knowledge docs based on prompt content.

If the prompt mentions a dependency (e.g. "npm install"), we inject `STACK.md`.
If it mentions architecture / design, we inject `DESIGN.md`. Cheap keyword
matching keeps latency negligible; the heavier semantic-retrieval path lives
in the memory MCP server (`/recall`).

M1 baseline: simple keyword → file mapping. M3+ will add a memory.recall()
call against the prompt for facts above a relevance threshold.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, project_root, read_payload, write_output

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


def main() -> int:
    payload = read_payload()
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0

    docs_dir = _knowledge_dir(payload)
    if not docs_dir.is_dir():
        return 0

    matched: list[str] = []
    for pattern, doc in KEYWORD_TO_DOC:
        if pattern.search(prompt) and (docs_dir / doc).exists() and doc not in matched:
            matched.append(doc)

    if not matched:
        return 0

    # Cap total injection size to keep token budget under control.
    sections: list[str] = []
    budget = 4000
    used = 0
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

    if not sections:
        return 0

    log(payload, f"injected knowledge docs: {matched}")
    write_output({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n---\n\n".join(sections),
        }
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
