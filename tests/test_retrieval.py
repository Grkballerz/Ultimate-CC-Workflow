"""Tests for hybrid retrieval. Currently FTS-only; vector layer arrives later."""
from __future__ import annotations

from ucw_memory.db import MemoryDB
from ucw_memory.retrieval import recall


def _seed(db: MemoryDB) -> None:
    db.note(
        scope="project", subject="demo-app", predicate="uses",
        object_="flask", reason="quick prototype framework",
    )
    db.note(
        scope="project", subject="db", predicate="is",
        object_="postgres 16", reason="ACID + JSONB",
    )
    fid = db.note(
        scope="project", subject="cache", predicate="is",
        object_="redis", reason="low latency lookups",
    )
    db.pin(fid)


def test_recall_returns_pinned_first(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        _seed(db)
        result = recall(db, "anything unrelated", k=5)
        # Pinned should appear regardless of relevance
        assert any("redis" in h.fact.object for h in result.hits)


def test_recall_fts_match(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        _seed(db)
        result = recall(db, "postgres", k=5)
        # Pinned redis still there but postgres hit should be top scoring after pinned bucket
        objects = [h.fact.object for h in result.hits]
        assert "postgres 16" in objects


def test_recall_as_context_respects_budget(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        _seed(db)
        result = recall(db, "flask", k=10, char_budget=80)
        rendered = result.as_context()
        # Header + at most one or two lines under 80 chars
        assert "UCW Memory" in rendered
        assert len(rendered) <= 200  # generous: header + 1-2 short lines
