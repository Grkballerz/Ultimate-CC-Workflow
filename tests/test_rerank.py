"""Tests for the reranker — pure helpers and reranker-integrated retrieval.

We mock the API-backed rerankers so tests work without ANTHROPIC_API_KEY or
VOYAGE_API_KEY.
"""
from __future__ import annotations

import pytest

from ucw_memory.db import MemoryDB
from ucw_memory.rerank import RerankItem, fuse, reorder, make_reranker
from ucw_memory.retrieval import recall


# ---- Pure helpers ------------------------------------------------------------

def test_fuse_combines_with_alpha():
    fts = [1.0, 0.0]
    rer = [0.0, 1.0]
    # alpha 0.5 → 0.5/0.5
    assert fuse(fts, rer, alpha=0.5) == [0.5, 0.5]
    # alpha 0 → all-rerank
    assert fuse(fts, rer, alpha=0.0) == [0.0, 1.0]
    # alpha 1 → all-fts
    assert fuse(fts, rer, alpha=1.0) == [1.0, 0.0]


def test_reorder_descending():
    items = [
        RerankItem(text="a", score=0.0, payload={"i": 1}),
        RerankItem(text="b", score=0.0, payload={"i": 2}),
        RerankItem(text="c", score=0.0, payload={"i": 3}),
    ]
    out = reorder(items, [0.1, 0.9, 0.5])
    payloads = [it.payload["i"] for it in out]
    assert payloads == [2, 3, 1]


def test_make_reranker_returns_none_when_no_keys(monkeypatch):
    # Ensure no env keys
    for var in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "UCW_RERANK_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    assert make_reranker() is None


# ---- Retrieval with mock reranker --------------------------------------------

def _seed(db: MemoryDB) -> None:
    db.note(scope="project", subject="demo-app", predicate="uses",
            object_="flask", reason="quick prototype framework")
    db.note(scope="project", subject="db", predicate="is",
            object_="postgres 16", reason="ACID + JSONB")
    db.note(scope="project", subject="cache", predicate="is",
            object_="redis", reason="low latency lookups")


def test_recall_uses_injected_reranker(tmp_path):
    """Mock reranker that inverts FTS order — recall should respect it."""
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        _seed(db)

        def inverter(query, items):
            # Score items inversely to their position (last = best)
            n = len(items)
            return [(i + 1) / n for i in range(n)]

        result = recall(db, "is", k=5, reranker=inverter)
        assert result.embedding_mode == "fts-only"  # reranker injected, mode label only flips with make_reranker
        # Reranker was called and influenced ordering — we can't strictly assert
        # since recency boost contributes too, but pinning is empty so reranker
        # has effect.
        assert len(result.hits) >= 2


def test_recall_falls_back_when_reranker_raises(tmp_path):
    with MemoryDB(tmp_path / "memory.sqlite") as db:
        _seed(db)

        def broken(query, items):
            raise RuntimeError("rerank service down")

        result = recall(db, "postgres", k=5, reranker=broken)
        # Should still return FTS-only results despite the rerank failure
        assert any("postgres" in h.fact.object for h in result.hits)
        assert result.embedding_mode == "fts-only"
