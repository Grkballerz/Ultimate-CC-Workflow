#!/usr/bin/env python3
"""Surface recurring patterns from `facts` → draft Skill files for review.

The pipeline:
1. Group facts by (predicate, object_token). A "token" is the first
   significant noun in the object — e.g. "postgres 16" → "postgres".
2. For each group with ≥ N occurrences, compute confidence as the fraction
   of distinct sessions that referenced the pattern.
3. Write each pattern into `instincts` if it isn't already there.
4. For instincts above a promotion threshold, draft a `skills/promoted/<slug>/`
   directory with SKILL.md and INSTRUCTIONS.md from a template.

Pure stdlib. Doesn't touch the LLM — promotion drafts are starting points
for the user to edit, not finished skills.

Usage:
    ucw-distill-instincts.py                       # default: scan project DB
    ucw-distill-instincts.py --min-uses 3 --promote 0.8
    ucw-distill-instincts.py --dry-run             # show would-be writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "memory"))

from ucw_memory.db import MemoryDB

# ---- helpers -----------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "for", "to", "and",
    "or", "but", "with", "on", "in", "at", "by", "as", "it", "its",
}


def _object_token(text: str) -> str | None:
    """First significant word of the object — used to group similar facts."""
    for word in re.findall(r"[A-Za-z][A-Za-z0-9\-_.]*", text):
        if word.lower() not in _STOPWORDS:
            return word.lower()
    return None


def _project_db() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir():
            return parent / ".ucw" / "memory.sqlite"
    return cwd / ".ucw" / "memory.sqlite"


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:48] or "instinct"


# ---- core --------------------------------------------------------------------

def aggregate(db: MemoryDB, *, scope: str = "project") -> list[dict]:
    """Return aggregated instinct candidates from the facts table."""
    facts = db.list_facts(scope=scope, limit=5000)
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for f in facts:
        token = _object_token(f.object)
        if not token:
            continue
        key = (f.predicate.lower(), token)
        groups[key].append(f)

    out: list[dict] = []
    for (predicate, token), members in groups.items():
        sessions = {m.source_session for m in members if m.source_session}
        uses = len(members)
        # Confidence grows with evidence: a session-diversity factor and a
        # sample-size factor, each saturating toward 1.0. The old formula
        # ((s + 1) / (n + 2)) was inverted — MORE uses LOWERED confidence.
        session_count = max(len(sessions), 1)
        confidence = (session_count / (session_count + 1)) * (uses / (uses + 1))
        # Build human-friendly pattern/trigger from the members
        sample = members[0]
        out.append({
            "pattern": f"{sample.subject} {sample.predicate} {sample.object}",
            "trigger": token,
            "predicate": predicate,
            "uses": uses,
            "sessions": session_count,
            "confidence": round(min(confidence, 1.0), 3),
            "example_reasons": list({m.reason for m in members})[:3],
            "fact_ids": [m.id for m in members],
        })
    out.sort(key=lambda d: (d["uses"], d["confidence"]), reverse=True)
    return out


def upsert_instincts(
    db: MemoryDB,
    candidates: list[dict],
    *,
    min_uses: int,
) -> tuple[int, int]:
    """Write candidates into `instincts` table. Returns (inserted, updated)."""
    inserted = 0
    updated = 0
    conn = db.conn
    for c in candidates:
        if c["uses"] < min_uses:
            continue
        existing = conn.execute(
            "SELECT id, uses FROM instincts WHERE pattern = ? AND trigger = ?",
            (c["pattern"], c["trigger"]),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO instincts(pattern, trigger, confidence, uses, last_used)
                   VALUES (?, ?, ?, ?, ?)""",
                (c["pattern"], c["trigger"], c["confidence"], c["uses"], int(time.time())),
            )
            inserted += 1
        else:
            conn.execute(
                """UPDATE instincts
                      SET confidence = ?, uses = ?, last_used = ?
                    WHERE id = ?""",
                (c["confidence"], c["uses"], int(time.time()), existing[0]),
            )
            updated += 1
    conn.commit()
    return inserted, updated


SKILL_TEMPLATE = """\
---
name: {slug}
description: {description}
when_to_use: Pattern detected across {uses} mentions in {sessions} session(s).
source: ucw-distill-instincts
confidence: {confidence}
---

# {title}

Auto-drafted from a recurring pattern in memory. **Review before keeping.**

## Pattern

`{pattern}`

## Why this matters

Observed reasons (sample):

{reason_bullets}

## Instructions

_(replace with the canonical recipe for handling this pattern — what to do
when you see `{trigger}`, what to avoid, what tools to use)_
"""


def draft_skill_files(
    candidates: list[dict],
    *,
    repo_root: Path,
    threshold: float,
) -> list[Path]:
    """Write skills/promoted/<slug>/SKILL.md for each candidate above threshold."""
    skills_dir = repo_root / "skills" / "promoted"
    written: list[Path] = []
    for c in candidates:
        if c["confidence"] < threshold:
            continue
        slug = _slug(c["pattern"])
        skill_path = skills_dir / slug / "SKILL.md"
        if skill_path.exists():
            continue
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        reason_bullets = "\n".join(f"- {r}" for r in c["example_reasons"]) or "- _(no reason text)_"
        body = SKILL_TEMPLATE.format(
            slug=slug,
            description=f"Promoted instinct: {c['pattern']}",
            title=c["pattern"],
            pattern=c["pattern"],
            trigger=c["trigger"],
            uses=c["uses"],
            sessions=c["sessions"],
            confidence=c["confidence"],
            reason_bullets=reason_bullets,
        )
        skill_path.write_text(body, encoding="utf-8")
        written.append(skill_path)
    return written


# ---- CLI ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-distill-instincts")
    parser.add_argument("--db", help="path to memory DB (default: ./.ucw/memory.sqlite)")
    parser.add_argument("--scope", default="project", choices=["project", "global"])
    parser.add_argument("--min-uses", type=int, default=3,
                        help="minimum uses before writing to instincts table (default 3)")
    parser.add_argument("--promote", type=float, default=0.7,
                        help="confidence threshold for drafting a Skill file (default 0.7)")
    parser.add_argument("--repo", default=os.getcwd(),
                        help="repo root for skills/promoted/ output")
    parser.add_argument("--dry-run", action="store_true",
                        help="show aggregates without writing instincts or skills")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else _project_db()
    if not db_path.exists():
        print(f"no memory DB at {db_path} — run /ucw init", file=sys.stderr)
        return 2

    with MemoryDB(db_path) as db:
        candidates = aggregate(db, scope=args.scope)
        if args.dry_run:
            payload = {"candidates": candidates}
            if args.json:
                json.dump(payload, sys.stdout, indent=2)
            else:
                print(f"would write {sum(1 for c in candidates if c['uses'] >= args.min_uses)} instincts; "
                      f"would draft {sum(1 for c in candidates if c['confidence'] >= args.promote)} skills")
                for c in candidates[:20]:
                    print(f"  uses={c['uses']:>3} conf={c['confidence']:.2f} {c['pattern']}")
            return 0

        ins, upd = upsert_instincts(db, candidates, min_uses=args.min_uses)
        drafts = draft_skill_files(
            [c for c in candidates if c["confidence"] >= args.promote],
            repo_root=Path(args.repo).resolve(),
            threshold=args.promote,
        )

    result = {
        "instincts_inserted": ins,
        "instincts_updated":  upd,
        "skills_drafted":     [str(p) for p in drafts],
        "candidates_total":   len(candidates),
    }
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"instincts: +{ins} new, ~{upd} updated")
        print(f"skills drafted: {len(drafts)}")
        for p in drafts:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
