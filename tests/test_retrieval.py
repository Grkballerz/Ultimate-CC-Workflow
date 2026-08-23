"""Tests for hybrid retrieval. Currently FTS-only; vector layer arrives later."""
from __future__ import annotations

import time

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


# ---- pin quota (QW4) ---------------------------------------------------------

def _set_created_at(db: MemoryDB, fact_id: int, created_at: int) -> None:
    db.conn.execute("UPDATE facts SET created_at = ? WHERE id = ?", (created_at, fact_id))
    db.conn.commit()


def test_pin_quota_leaves_query_slots_at_k12_with_15_pins(tmp_path):
    """15 pins must not crowd out query hits: ceil(12/3)=4 reserved pin slots,
    >= 8 slots left for query-ranked facts."""
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        now = int(time.time())
        pin_ids = []
        for i in range(15):
            fid = db.note(scope="project", subject=f"convention-{i}", predicate="is",
                          object_=f"pinned-rule-{i}", reason=f"team standard {i}")
            _set_created_at(db, fid, now - 1000 + i)  # later i = more recent
            db.pin(fid)
            pin_ids.append(fid)
        for i in range(10):
            db.note(scope="project", subject=f"service-{i}", predicate="uses",
                    object_=f"redis shard {i}", reason=f"low latency lookups {i}")

        result = recall(db, "redis shard", k=12)

    pinned_hits = [h for h in result.hits if h.sources == ("pinned",)]
    query_hits = [h for h in result.hits if "fts" in h.sources]
    assert len(result.hits) == 12
    assert len(pinned_hits) == 4  # ceil(12/3)
    assert len(query_hits) >= 8
    # Most recent pins win the reserved slots
    assert {h.fact.id for h in pinned_hits} == set(pin_ids[-4:])


def test_pinned_fact_beyond_cap_still_ranks_in_query_pool(tmp_path):
    """A pin that loses its reserved slot still surfaces via FTS when it
    genuinely matches the query."""
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        now = int(time.time())
        old_pin = db.note(scope="project", subject="db", predicate="uses",
                          object_="postgres tuning notes", reason="slow query audit")
        _set_created_at(db, old_pin, now - 5000)
        db.pin(old_pin)
        for i in range(4):
            fid = db.note(scope="project", subject=f"rule-{i}", predicate="is",
                          object_=f"unrelated-pin-{i}", reason=f"r{i}")
            _set_created_at(db, fid, now - i)
            db.pin(fid)

        # k=6 → cap 2: the two newest pins are reserved; old_pin competes via FTS.
        result = recall(db, "postgres", k=6)

    hit = next(h for h in result.hits if h.fact.id == old_pin)
    assert "fts" in hit.sources
    assert hit.sources != ("pinned",)
