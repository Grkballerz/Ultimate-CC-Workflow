"""Rerank candidate facts against a query.

Two backends, selected at runtime:

1. **Claude reranker** (preferred when `embedding_provider = claude`) — uses
   Claude Haiku as the semantic judge. One batched call per query: Claude
   scores each candidate 0-10. This is the literal "embedding model is Claude"
   path — semantic reasoning replaces vector embeddings.

2. **Voyage reranker** (`embedding_provider = voyage`) — uses Voyage's
   `rerank-2` model. Faster and slightly more precise on dense semantic match.

Both are optional; if no dependency / key is present, retrieval falls back
to FTS+recency ranking from `retrieval.py` alone.

The module is import-safe even without `anthropic` / `voyageai` installed;
clients should call `is_available()` and provide a fallback path.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

# Try imports lazily so the package works without anthropic/voyage installed.
try:
    import anthropic  # type: ignore[import-not-found]
    _HAS_ANTHROPIC = True
except ImportError:
    anthropic = None  # type: ignore[assignment]
    _HAS_ANTHROPIC = False

try:
    import voyageai  # type: ignore[import-not-found]
    _HAS_VOYAGE = True
except ImportError:
    voyageai = None  # type: ignore[assignment]
    _HAS_VOYAGE = False


@dataclass(frozen=True)
class RerankItem:
    text: str
    score: float
    payload: dict | None = None  # caller-provided metadata (e.g. fact id)


class Reranker(Protocol):
    def rerank(self, query: str, items: list[str]) -> list[float]: ...


# ---- Claude (Haiku) reranker -------------------------------------------------

_CLAUDE_SYSTEM_PROMPT = (
    "You are a relevance judge. For each candidate fact, decide how relevant "
    "it is to the user's query on a scale 0..10 (0=unrelated, 10=exactly answers "
    "the query). Output a single JSON object: "
    "{\"scores\": [<int>, <int>, ...]} matching the input order. "
    "Do not output anything else."
)


class ClaudeReranker:
    """Use Claude Haiku as a relevance judge.

    Costs one API call per `rerank()`. Should be fed at most ~30 candidates;
    callers must pre-filter via FTS+recency first.
    """

    def __init__(self, *, model: str = "claude-haiku-4-5", api_key: str | None = None):
        if not _HAS_ANTHROPIC:
            raise RuntimeError("anthropic package not installed — pip install ucw-memory[claude]")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=key)  # type: ignore[union-attr]
        self.model = model

    def rerank(self, query: str, items: list[str]) -> list[float]:
        if not items:
            return []
        # Cap at 30 to keep the single-call latency reasonable.
        items = items[:30]
        prompt = self._build_prompt(query, items)
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=_CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        try:
            data = json.loads(text)
            scores = [float(s) / 10.0 for s in data["scores"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return [0.5] * len(items)
        if len(scores) != len(items):
            return [0.5] * len(items)
        return scores

    @staticmethod
    def _build_prompt(query: str, items: list[str]) -> str:
        numbered = "\n".join(f"{i+1}. {x}" for i, x in enumerate(items))
        return f"QUERY: {query}\n\nCANDIDATES:\n{numbered}\n\nReturn JSON now."


# ---- Voyage reranker ---------------------------------------------------------

class VoyageReranker:
    def __init__(self, *, model: str = "rerank-2", api_key: str | None = None):
        if not _HAS_VOYAGE:
            raise RuntimeError("voyageai package not installed — pip install ucw-memory[voyage]")
        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise RuntimeError("VOYAGE_API_KEY not set")
        self.client = voyageai.Client(api_key=key)  # type: ignore[union-attr]
        self.model = model

    def rerank(self, query: str, items: list[str]) -> list[float]:
        if not items:
            return []
        response = self.client.rerank(query=query, documents=items, model=self.model)
        # Voyage returns results with .relevance_score per doc, in *input* order.
        # (The SDK may sort; we re-key by `index`.)
        scores = [0.0] * len(items)
        for r in response.results:
            scores[r.index] = float(r.relevance_score)
        return scores


# ---- Factory -----------------------------------------------------------------

def make_reranker(provider: str | None = None) -> Reranker | None:
    """Best-effort reranker factory. `provider` overrides env detection.

    Returns None when nothing is available — callers should skip the rerank
    step gracefully.
    """
    provider = (provider or os.environ.get("UCW_RERANK_PROVIDER") or "").lower()
    if provider == "voyage" and _HAS_VOYAGE and os.environ.get("VOYAGE_API_KEY"):
        try:
            return VoyageReranker()
        except RuntimeError:
            pass
    if provider == "claude" and _HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeReranker()
        except RuntimeError:
            pass
    # Auto: prefer Voyage if available, else Claude.
    if not provider:
        if _HAS_VOYAGE and os.environ.get("VOYAGE_API_KEY"):
            try:
                return VoyageReranker()
            except RuntimeError:
                pass
        if _HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                return ClaudeReranker()
            except RuntimeError:
                pass
    return None


def is_available(provider: str | None = None) -> bool:
    return make_reranker(provider) is not None


# ---- Pure helpers (testable without API keys) -------------------------------

def fuse(fts_scores: list[float], rerank_scores: list[float], *, alpha: float = 0.4) -> list[float]:
    """Convex-combine FTS and rerank scores.

    alpha = weight on FTS, (1-alpha) on rerank. Defaults toward rerank since
    semantic relevance usually beats lexical match once we have both.
    """
    assert len(fts_scores) == len(rerank_scores), "score arrays must align"
    return [alpha * f + (1 - alpha) * r for f, r in zip(fts_scores, rerank_scores)]


def reorder(items: list[RerankItem], scores: list[float]) -> list[RerankItem]:
    """Return items sorted by score descending. Stable for ties."""
    paired = list(zip(items, scores))
    paired.sort(key=lambda p: p[1], reverse=True)
    return [
        RerankItem(text=it.text, score=s, payload=it.payload)
        for it, s in paired
    ]
