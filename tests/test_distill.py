"""Tests for the heuristic distiller — privacy stripping, fact extraction, DB write."""
from __future__ import annotations

import json

from ucw_memory.db import MemoryDB
from ucw_memory.distill import (
    extract_from_text,
    extract_from_transcript,
    write_candidates_to_db,
)
from ucw_memory.privacy import strip_private


def test_strip_private_removes_secret_tags():
    text = "we use postgres <secret>password is hunter2</secret> in prod"
    out = strip_private(text)
    assert "hunter2" not in out
    assert "postgres" in out


def test_strip_private_handles_multiline_and_case():
    text = "before\n<PRIVATE>\nmulti\nline\n</PRIVATE>after"
    out = strip_private(text)
    assert "multi" not in out
    assert "before" in out
    assert "after" in out


def test_extract_basic_decision_with_reason():
    text = "We chose to use postgres because it has JSONB support."
    cands = extract_from_text(text)
    assert len(cands) == 1
    c = cands[0]
    assert c.subject.lower() == "we"
    assert "use" in c.predicate
    assert "postgres" in c.object.lower()
    assert "JSONB" in c.reason


def test_extract_rejects_bare_conclusion():
    text = "We use postgres."  # no reason connective → dropped
    assert extract_from_text(text) == []


def test_extract_skips_private_region():
    text = (
        "<private>We use postgres because it's fast.</private> "
        "Outside the tag we chose redis because of low latency."
    )
    cands = extract_from_text(text)
    objects = [c.object.lower() for c in cands]
    assert any("redis" in o for o in objects)
    assert not any("postgres" in o for o in objects)


def test_extract_handles_multiple_in_one_paragraph():
    text = (
        "We use pnpm because the lockfile is smaller. "
        "The user prefers ruff since it's faster than flake8."
    )
    cands = extract_from_text(text)
    objects = [c.object.lower() for c in cands]
    assert any("pnpm" in o for o in objects)
    assert any("ruff" in o for o in objects)


def test_extract_from_transcript(tmp_path):
    transcript = tmp_path / "session.jsonl"
    events = [
        {"message": {"content": "Hi, what stack are we on?"}},
        {"message": {"content": [
            {"type": "text", "text": "We chose to use flask because it's quick to prototype."}
        ]}},
        {"prompt": "Also we will use pytest because it's the team default."},
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in events))
    cands = extract_from_transcript(transcript)
    objects = [c.object.lower() for c in cands]
    assert any("flask" in o for o in objects)
    assert any("pytest" in o for o in objects)


def test_write_candidates_to_db_round_trip(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"message": {"content": "We use bun because it's fast."}}))

    db_path = tmp_path / "memory.sqlite"
    cands = extract_from_transcript(transcript)
    with MemoryDB(db_path) as db:
        ids = write_candidates_to_db(cands, db, scope="project")
        assert len(ids) == 1
        # FTS should retrieve it back
        hits = db.fts_search("bun", limit=5)
        assert len(hits) == 1
        assert "bun" in hits[0][0].object.lower()
