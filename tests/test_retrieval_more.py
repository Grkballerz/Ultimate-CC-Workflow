"""Extra retrieval coverage: empty DB, empty query, recency decay,
char-budget clipping with many hits, multi-scope merging via MCP."""
from __future__ import annotations

import time

from ucw_memory.db import MemoryDB
from ucw_memory.retrieval import _normalize, _recency_boost, recall


def test_recall_empty_db_returns_empty(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        result = recall(db, "anything")
        assert result.hits == []
        assert "no memory matches" in result.as_context()


def test_recall_empty_query_no_fts_pinned_only(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        fid = db.note(scope="project", subject="user", predicate="prefers",
                      object_="vim", reason="muscle memory")
        db.pin(fid)
        # Empty query: no FTS pool but pinned should surface
        result = recall(db, "   ", k=5)
        assert any(h.fact.id == fid for h in result.hits)


def test_recall_clips_to_char_budget(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        for i in range(20):
            db.note(scope="project", subject=f"s{i}", predicate="is",
                    object_=f"o{i} thing", reason=f"r{i}")
        result = recall(db, "thing", k=20, char_budget=120)
        ctx = result.as_context()
        # Header line ("## UCW Memory (recalled)") plus a small number of hits
        assert len(ctx) <= 220
        assert "UCW Memory" in ctx


def test_recency_boost_decays():
    # Build fake facts with known created_at
    from ucw_memory.db import Fact
    now = int(time.time())
    fresh = Fact(0, "p", "is", "x", "y", "z", None, None, 0.7, now, None, False)
    old   = Fact(0, "p", "is", "x", "y", "z", None, None, 0.7, now - 86400 * 28, None, False)
    assert _recency_boost(fresh) > 0.9
    assert _recency_boost(old) < 0.3  # 4 weeks → about 0.25 (two half-lives)


def test_normalize_degenerate_returns_midpoint():
    assert _normalize([]) == []
    assert _normalize([5.0]) == [0.5]
    assert _normalize([3.0, 3.0, 3.0]) == [0.5, 0.5, 0.5]


def test_normalize_min_max():
    out = _normalize([1.0, 2.0, 4.0])
    assert out[0] == 0.0
    assert out[-1] == 1.0
    assert 0 < out[1] < 1
