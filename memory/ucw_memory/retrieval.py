"""Hybrid retrieval: FTS5 + (optional) reranking via Claude or Voyage.

Behavior:
1. **Pinned facts** always included first, sorted by recency.
2. **FTS5** pulls a pool of up to `fts_pool` BM25 hits.
3. **Reranker** (if available) scores the FTS pool semantically and re-sorts.
4. **Recency boost** breaks ties.

The reranker is auto-detected via `rerank.make_reranker()`. If no key is set,
the pipeline runs FTS-only and reports `embedding_mode = "fts-only"`. The
literal "embedding model is Claude" path corresponds to
`embedding_mode = "fts+rerank-claude"`.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .db import Fact, MemoryDB
from .rerank import fuse, make_reranker


@dataclass(frozen=True)
class RecallHit:
    fact: Fact
    score: float
    sources: tuple[str, ...]  # ("pinned",) | ("fts",) | ("fts","rerank")


@dataclass
class RecallResult:
    hits: list[RecallHit]
    char_budget: int
    embedding_mode: str  # "fts-only" | "fts+rerank-claude" | "fts+rerank-voyage"

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


# A reranker is a callable taking (query, list[str]) and returning list[float].
RerankFn = Callable[[str, list[str]], list[float]]


def recall(
    db: MemoryDB,
    query: str,
    *,
    k: int = 12,
    char_budget: int = 4000,
    scope: str | None = None,
    fts_pool: int = 30,
    reranker: RerankFn | None = None,
    rerank_provider: str | None = None,
) -> RecallResult:
    """Retrieve top-k facts for query.

    `reranker` lets tests inject a mock; production code passes None and we
    auto-detect via `rerank.make_reranker(rerank_provider)`.
    """
    pinned = db.pinned_facts(scope=scope)
    pinned_hits = [
        RecallHit(fact=f, score=10.0 + (1.0 / (1 + _age_seconds(f))), sources=("pinned",))
        for f in pinned
    ]

    fts_hits_raw = db.fts_search(query, limit=fts_pool, scope=scope) if query.strip() else []
    fts_facts = [
        (f, s) for f, s in fts_hits_raw
        if not any(p.fact.id == f.id for p in pinned_hits)
    ]

    # Decide whether to rerank.
    embedding_mode = "fts-only"
    rerank_fn = reranker
    if rerank_fn is None and fts_facts:
        r = make_reranker(rerank_provider)
        if r is not None:
            rerank_fn = r.rerank
            embedding_mode = f"fts+rerank-{r.__class__.__name__.replace('Reranker', '').lower()}"

    if rerank_fn is not None and fts_facts:
        items = [f.as_text() for f, _ in fts_facts]
        fts_norm = _normalize([s for _, s in fts_facts])
        try:
            rerank_scores = rerank_fn(query, items)
            fused = fuse(fts_norm, rerank_scores)
            fts_hits = [
                RecallHit(fact=f, score=score + _recency_boost(f), sources=("fts", "rerank"))
                for (f, _), score in zip(fts_facts, fused, strict=True)
            ]
        except Exception:
            # Any reranker error: silently fall back to FTS-only scoring.
            fts_hits = [
                RecallHit(fact=f, score=s + _recency_boost(f), sources=("fts",))
                for f, s in fts_facts
            ]
            embedding_mode = "fts-only"
    else:
        fts_hits = [
            RecallHit(fact=f, score=s + _recency_boost(f), sources=("fts",))
            for f, s in fts_facts
        ]

    merged = pinned_hits + fts_hits
    merged.sort(key=lambda h: h.score, reverse=True)

    return RecallResult(
        hits=merged[:k],
        char_budget=char_budget,
        embedding_mode=embedding_mode,
    )


def _normalize(scores: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]. Degenerate cases return all 0.5."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi <= lo:
        return [0.5] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def _age_seconds(fact: Fact, now: int | None = None) -> int:
    now = now if now is not None else int(time.time())
    return max(0, now - fact.created_at)


def _recency_boost(fact: Fact, *, half_life_days: float = 14.0) -> float:
    """Exponential decay boost in [0, 1]. Half-life 2 weeks by default."""
    age_days = _age_seconds(fact) / 86400.0
    return 0.5 ** (age_days / half_life_days)


# Make pathlib.Path importable for tests that monkey-patch.
from pathlib import Path  # noqa: E402,F401
