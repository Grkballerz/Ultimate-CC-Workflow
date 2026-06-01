"""Unit tests for the MemoryDB layer."""
from __future__ import annotations

import time

import pytest
from ucw_memory.db import MemoryDB


def _make_db(tmp_path) -> MemoryDB:
    return MemoryDB(tmp_path / "memory.sqlite")


def test_note_requires_reason(tmp_path):
    with _make_db(tmp_path) as db:
        with pytest.raises(ValueError):
            db.note(
                scope="project",
                subject="demo",
                predicate="uses",
                object_="flask",
                reason="   ",  # bare conclusion — quality gate
            )


def test_note_pin_recall(tmp_path):
    with _make_db(tmp_path) as db:
        fid = db.note(
            scope="project",
            subject="demo-app",
            predicate="uses",
            object_="flask",
            reason="quick prototype framework",
            confidence=0.9,
        )
        assert fid > 0

        db.pin(fid)
        pinned = db.pinned_facts(scope="project")
        assert any(f.id == fid for f in pinned)


def test_fts_search(tmp_path):
    with _make_db(tmp_path) as db:
        db.note(
            scope="project", subject="db", predicate="is",
            object_="postgres 16", reason="ACID + JSONB",
        )
        db.note(
            scope="project", subject="cache", predicate="is",
            object_="redis", reason="low latency lookups",
        )
        hits = db.fts_search("postgres", limit=5)
        assert len(hits) == 1
        assert hits[0][0].object == "postgres 16"


def test_forget_excludes_from_searches(tmp_path):
    with _make_db(tmp_path) as db:
        fid = db.note(
            scope="project", subject="x", predicate="is", object_="dropped",
            reason="will be forgotten",
        )
        db.forget(fid)
        assert db.by_id(fid) is None
        assert db.fts_search("dropped") == []


def test_expire_drops_ttl_unpinned(tmp_path):
    with _make_db(tmp_path) as db:
        old = db.note(
            scope="project", subject="o", predicate="is", object_="old",
            reason="ttl expired", ttl_seconds=1,
        )
        kept_pinned = db.note(
            scope="project", subject="p", predicate="is", object_="pinned",
            reason="never expire", ttl_seconds=1, pinned=True,
        )
        # Force "now" forward
        n = db.expire(now=int(time.time()) + 3600)
        assert n == 1
        assert db.by_id(old) is None
        assert db.by_id(kept_pinned) is not None
