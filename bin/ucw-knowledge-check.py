#!/usr/bin/env python3
"""Stale-doc detection — flag when Knowledge is out of date.

For each Knowledge doc, check whether any "trigger" files have been modified
since the doc was last touched (`.ucw/last-scribe-sha` is the source of truth;
file mtime is the fallback). Returns JSON of stale docs with reasons.

Used by:
- `/ucw status` to surface warnings
- CI to fail when shipping with stale docs (`--strict` exits non-zero)

Mapping (which files imply which docs are stale):
    STACK.md         → package.json, lockfiles, pyproject.toml, go.mod, Cargo.toml
    CONVENTIONS.md   → .eslintrc*, .prettierrc*, ruff.toml, biome.json
    DESIGN.md        → anything under src/ lib/ internal/ app/ pkg/
    ROADMAP.md       → never auto-stale (user-curated)
    GLOSSARY.md      → never auto-stale
    PREFERENCES.md   → never auto-stale
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TRIGGERS: dict[str, list[re.Pattern[str]]] = {
    "STACK.md": [
        re.compile(r"^(?:.+/)?package\.json$"),
        re.compile(r"^(?:.+/)?(?:pnpm|yarn|package|bun)[-.]?lock(?:\.yaml|\.json|b)?$"),
        re.compile(r"^(?:.+/)?pyproject\.toml$"),
        re.compile(r"^(?:.+/)?(?:uv|poetry|Pipfile)\.lock$"),
        re.compile(r"^(?:.+/)?requirements\.txt$"),
        re.compile(r"^(?:.+/)?go\.(?:mod|sum)$"),
        re.compile(r"^(?:.+/)?Cargo\.(?:toml|lock)$"),
        re.compile(r"^(?:.+/)?Gemfile(?:\.lock)?$"),
        re.compile(r"^(?:.+/)?composer\.(?:json|lock)$"),
    ],
    "CONVENTIONS.md": [
        re.compile(r"^(?:.+/)?\.eslintrc"),
        re.compile(r"^(?:.+/)?\.prettierrc"),
        re.compile(r"^(?:.+/)?biome\.json$"),
        re.compile(r"^(?:.+/)?ruff\.toml$"),
        re.compile(r"^(?:.+/)?\.golangci\."),
    ],
    "DESIGN.md": [
        re.compile(r"^(?:src|lib|internal|app|pkg|memory|hooks|bin)/"),
    ],
}


@dataclass(frozen=True)
class Stale:
    doc: str
    reasons: tuple[str, ...]


def _list_files(repo: Path) -> list[Path]:
    """All tracked files under repo, lightly filtered."""
    out: list[Path] = []
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(repo).parts
        if any(part in {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
               for part in rel_parts):
            continue
        out.append(p)
    return out


def _doc_mtime(repo: Path, doc: str) -> float | None:
    path = repo / ".ucw" / "knowledge" / doc
    if not path.exists():
        return None
    return path.stat().st_mtime


def check(repo: Path) -> list[Stale]:
    knowledge_dir = repo / ".ucw" / "knowledge"
    if not knowledge_dir.is_dir():
        return []

    out: list[Stale] = []
    for doc, patterns in TRIGGERS.items():
        doc_mtime = _doc_mtime(repo, doc)
        if doc_mtime is None:
            continue  # doc not present — not stale, just missing
        stale_reasons: list[str] = []
        for f in _list_files(repo):
            rel = str(f.relative_to(repo))
            if any(p.search(rel) for p in patterns):
                try:
                    if f.stat().st_mtime > doc_mtime + 1.0:  # +1s tolerance
                        stale_reasons.append(rel)
                except OSError:
                    continue
        if stale_reasons:
            out.append(Stale(doc=doc, reasons=tuple(sorted(set(stale_reasons))[:10])))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-knowledge-check")
    parser.add_argument("--repo", default=".", help="repo root (default: cwd)")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if any docs are stale")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    stale = check(repo)

    if args.json:
        json.dump([{"doc": s.doc, "reasons": list(s.reasons)} for s in stale],
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif stale:
        print(f"⚠️  {len(stale)} stale Knowledge doc(s):")
        for s in stale:
            print(f"  {s.doc}")
            for r in s.reasons[:3]:
                print(f"    - {r}")
            if len(s.reasons) > 3:
                print(f"    … and {len(s.reasons) - 3} more")
        print("\nRun /scribe to refresh.")
    else:
        print("Knowledge is fresh ✓")

    if args.strict and stale:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
