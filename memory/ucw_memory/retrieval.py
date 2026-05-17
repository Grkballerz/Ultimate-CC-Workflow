"""Hybrid retrieval: FTS5 + (optional) vector ANN + RRF + reranking.

M3 lands FTS5 + recency + pinned-first. Vector and rerank layers are stubs
that activate when their optional deps are installed and the user has keys.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .db import Fact, MemoryDB


@dataclass(frozen=True)
class RecallHit:
    fact: Fact
    score: float
    sources: tuple[str, ...]  # which ranker(s) surfaced this — ("fts",), ("vec",), ("fts","vec")


@dataclass
class RecallResult:
    hits: list[RecallHit]
    char_budget: int
    embedding_mode: str  # "fts-only" | "fts+vec" | "fts+vec+rerank"

    def as_context(self) -> str:
        """Render hits as a markdown block suitable for SessionStart injection."""
        if not self.hits:
            return "_(no memory matches)_"
        lines = ["## UCW Memory (recalled)"]
        used = 0
        for hit in self.hits:
            line = f"- {hit.fact.as_text()}"
            if used + len(line) > self.char_budget:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)


def recall(
    db: MemoryDB,
    query: str,
    *,
    k: int = 12,
    char_budget: int = 4000,
    scope: str | None = None,
    fts_pool: int = 30,
) -> RecallResult:
    """Retrieve top-k facts for query using whatever rankers are available.

    Current behavior (M3 baseline):
        1. Always include pinned facts (most recent first), within budget.
        2. BM25 over FTS5 for the remaining slots.
        3. Recency boost: tie-break by created_at descending.

    Future:
        - Vector ANN via sqlite-vec when embeddings table is populated.
        - RRF merging FTS + vector ranks.
        - Voyage rerank-2 for top-N reordering.
        - Claude Haiku judge when no Voyage key.
    """
    pinned = db.pinned_facts(scope=scope)
    pinned_hits = [
        RecallHit(fact=f, score=10.0 + (1.0 / (1 + _age_seconds(f))), sources=("pinned",))
        for f in pinned
    ]

    fts_hits_raw = db.fts_search(query, limit=fts_pool, scope=scope) if query.strip() else []
    fts_hits = [
        RecallHit(fact=f, score=s + _recency_boost(f), sources=("fts",))
        for f, s in fts_hits_raw
        if not any(p.fact.id == f.id for p in pinned_hits)
    ]

    merged = pinned_hits + fts_hits
    merged.sort(key=lambda h: h.score, reverse=True)

    return RecallResult(
        hits=merged[:k],
        char_budget=char_budget,
        embedding_mode="fts-only",
    )


def _age_seconds(fact: Fact, now: int | None = None) -> int:
    now = now if now is not None else int(time.time())
    return max(0, now - fact.created_at)


def _recency_boost(fact: Fact, *, half_life_days: float = 14.0) -> float:
    """Exponential decay boost in [0, 1]. Half-life 2 weeks by default."""
    age_days = _age_seconds(fact) / 86400.0
    return 0.5 ** (age_days / half_life_days)
