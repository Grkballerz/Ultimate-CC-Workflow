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


# ---- WP3: realistic transcript phrasing --------------------------------------

def test_extract_contraction_subject_with_reason():
    text = "We're using pnpm because the lockfile is deterministic across CI runs."
    cands = extract_from_text(text)
    assert len(cands) == 1
    c = cands[0]
    assert c.subject == "we"
    assert "using" in c.predicate
    assert "pnpm" in c.object.lower()
    assert "lockfile" in c.reason
    assert c.confidence == 0.7


def test_extract_verb_first_pinned_line():
    """Terse decision note with no subject and no reason still extracts."""
    cands = extract_from_text("pinned TS to 5.x")
    assert len(cands) == 1
    c = cands[0]
    assert c.subject == "we"
    assert c.predicate == "pinned"
    assert c.object == "TS to 5.x"
    assert c.reason  # placeholder reason so the DB quality gate accepts it
    assert c.confidence < 0.7  # reduced confidence without an explicit reason


def test_extract_the_fix_was_to():
    cands = extract_from_text("The fix was to add a mutex around the token refresh.")
    assert len(cands) == 1
    c = cands[0]
    assert c.subject == "the fix"
    assert c.predicate == "was to"
    assert "mutex" in c.object.lower()


def test_extract_bullet_point_decision_line():
    text = "- switched to the forks pool because vitest workers crashed on ffmpeg\n"
    cands = extract_from_text(text)
    assert len(cands) == 1
    c = cands[0]
    assert c.predicate == "switched to"
    assert "forks pool" in c.object.lower()
    assert "vitest" in c.reason


def test_extract_module_path_subject():
    text = "bin/ucw-verify.py uses argparse because stdlib-only is a hard constraint."
    cands = extract_from_text(text)
    assert len(cands) == 1
    assert cands[0].subject == "bin/ucw-verify.py"
    assert "argparse" in cands[0].object


def test_extract_dotted_name_subject():
    text = "tailwind.config.mjs uses DESIGN.md tokens because it is the source of truth."
    cands = extract_from_text(text)
    assert len(cands) == 1
    assert cands[0].subject == "tailwind.config.mjs"


def test_extract_dont_use_imperative():
    cands = extract_from_text("Don't use eval because it's unsafe with user input.")
    assert len(cands) == 1
    c = cands[0]
    assert c.predicate == "don't use"
    assert c.object.lower() == "eval"
    assert "unsafe" in c.reason


def test_extract_weak_verb_without_reason_still_dropped():
    """Broadening must not relax the gate for weak verbs — 'we use X' alone
    stays dropped; only strong decision verbs pass reasonless."""
    assert extract_from_text("We use postgres in this project.") == []
    assert extract_from_text("The user prefers vim") == []


def test_extract_rejects_questions():
    assert extract_from_text("Should we switch to bun because it's faster?") == []
    assert extract_from_text("Why don't use we pnpm?") == []


def test_extract_rejects_hedged_proposals():
    assert extract_from_text("Maybe we should switch to bun because it's faster.") == []
    assert extract_from_text("Maybe we switched to bun because it's faster.") == []
    assert extract_from_text("Perhaps we're using the wrong pool because of threads.") == []


def test_reasonless_strong_decision_survives_db_quality_gate(tmp_path):
    """The placeholder reason must pass MemoryDB's non-empty-reason gate."""
    cands = extract_from_text("pinned TS to 5.x")
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        ids = write_candidates_to_db(cands, db, scope="project")
    assert len(ids) == 1


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
