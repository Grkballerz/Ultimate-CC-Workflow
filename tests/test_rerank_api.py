"""Cover the API-backed reranker paths using unittest.mock.

The pure helpers (fuse / reorder / make_reranker-with-no-keys) are covered
in test_rerank.py. This module mocks the anthropic and voyageai SDKs to
exercise ClaudeReranker and VoyageReranker without real keys.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from ucw_memory import rerank

# ---- ClaudeReranker ----------------------------------------------------------

@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a fake `anthropic` module into ucw_memory.rerank."""
    fake_mod = MagicMock()
    fake_client = MagicMock()
    # Return a message with one text block holding JSON scores
    fake_msg = MagicMock()
    fake_text_block = MagicMock()
    fake_text_block.type = "text"
    fake_text_block.text = '{"scores": [3, 9, 5]}'
    fake_msg.content = [fake_text_block]
    fake_client.messages.create.return_value = fake_msg
    fake_mod.Anthropic.return_value = fake_client
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(rerank, "anthropic", fake_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    return fake_mod, fake_client


def test_claude_reranker_rerank_returns_normalized_scores(fake_anthropic):
    r = rerank.ClaudeReranker()
    scores = r.rerank("query", ["a", "b", "c"])
    # Returned in [0..1], matching [3,9,5]/10
    assert scores == [0.3, 0.9, 0.5]


def test_claude_reranker_empty_items_returns_empty(fake_anthropic):
    r = rerank.ClaudeReranker()
    assert r.rerank("q", []) == []


def test_claude_reranker_falls_back_on_bad_json(monkeypatch):
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_text_block = MagicMock()
    fake_text_block.type = "text"
    fake_text_block.text = "not json at all"
    fake_msg.content = [fake_text_block]
    fake_client.messages.create.return_value = fake_msg
    fake_mod.Anthropic.return_value = fake_client
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(rerank, "anthropic", fake_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    r = rerank.ClaudeReranker()
    # On bad JSON, returns 0.5 for each — keeps the pipeline alive
    assert r.rerank("q", ["a", "b"]) == [0.5, 0.5]


def test_claude_reranker_falls_back_on_length_mismatch(monkeypatch):
    fake_mod = MagicMock()
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_text_block = MagicMock()
    fake_text_block.type = "text"
    # Returns 2 scores for 3 items → mismatch
    fake_text_block.text = '{"scores": [5, 5]}'
    fake_msg.content = [fake_text_block]
    fake_client.messages.create.return_value = fake_msg
    fake_mod.Anthropic.return_value = fake_client
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(rerank, "anthropic", fake_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    r = rerank.ClaudeReranker()
    assert r.rerank("q", ["a", "b", "c"]) == [0.5, 0.5, 0.5]


def test_claude_reranker_caps_at_30_items(fake_anthropic):
    _fake_mod, fake_client = fake_anthropic
    # Make the fake return a score array matching whatever we got
    def echo(*, model, max_tokens, system, messages):
        msg = MagicMock()
        # Count candidates in the prompt — each numbered "N. ..." line is one
        content = messages[0]["content"]
        lines = [ln for ln in content.split("\n") if ln and ln[0].isdigit() and ". " in ln]
        n = len(lines)
        block = MagicMock()
        block.type = "text"
        block.text = f'{{"scores": {[5] * n}}}'
        msg.content = [block]
        return msg
    fake_client.messages.create.side_effect = echo
    r = rerank.ClaudeReranker()
    # Pass 50 items; reranker should cap at 30
    scores = r.rerank("q", [f"item-{i}" for i in range(50)])
    assert len(scores) == 30


def test_claude_reranker_needs_api_key(monkeypatch):
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(rerank, "anthropic", MagicMock())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY not set"):
        rerank.ClaudeReranker()


def test_claude_reranker_needs_package(monkeypatch):
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", False)
    with pytest.raises(RuntimeError, match="anthropic package not installed"):
        rerank.ClaudeReranker()


# ---- VoyageReranker ----------------------------------------------------------

@pytest.fixture
def fake_voyage(monkeypatch):
    fake_mod = MagicMock()
    fake_client = MagicMock()

    class _Result:
        def __init__(self, index, score):
            self.index = index
            self.relevance_score = score

    fake_response = MagicMock()
    fake_response.results = [_Result(0, 0.2), _Result(1, 0.9), _Result(2, 0.4)]
    fake_client.rerank.return_value = fake_response
    fake_mod.Client.return_value = fake_client
    monkeypatch.setattr(rerank, "_HAS_VOYAGE", True)
    monkeypatch.setattr(rerank, "voyageai", fake_mod)
    monkeypatch.setenv("VOYAGE_API_KEY", "vy-fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return fake_mod, fake_client


def test_voyage_reranker_orders_by_index(fake_voyage):
    r = rerank.VoyageReranker()
    scores = r.rerank("q", ["a", "b", "c"])
    assert scores == [0.2, 0.9, 0.4]


def test_voyage_reranker_empty(fake_voyage):
    r = rerank.VoyageReranker()
    assert r.rerank("q", []) == []


def test_voyage_reranker_needs_key(monkeypatch):
    monkeypatch.setattr(rerank, "_HAS_VOYAGE", True)
    monkeypatch.setattr(rerank, "voyageai", MagicMock())
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY not set"):
        rerank.VoyageReranker()


def test_voyage_reranker_needs_package(monkeypatch):
    monkeypatch.setattr(rerank, "_HAS_VOYAGE", False)
    with pytest.raises(RuntimeError, match="voyageai package not installed"):
        rerank.VoyageReranker()


# ---- make_reranker factory ---------------------------------------------------

def test_make_reranker_prefers_voyage_when_both_available(fake_voyage, monkeypatch):
    # Also set fake anthropic to confirm voyage wins by default
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(rerank, "anthropic", MagicMock())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    r = rerank.make_reranker()
    assert isinstance(r, rerank.VoyageReranker)


def test_make_reranker_falls_back_to_claude_when_only_claude(fake_anthropic):
    r = rerank.make_reranker()
    assert isinstance(r, rerank.ClaudeReranker)


def test_make_reranker_explicit_provider_claude_picks_claude(fake_voyage, fake_anthropic):
    # Both available but caller asked for claude explicitly
    r = rerank.make_reranker(provider="claude")
    assert isinstance(r, rerank.ClaudeReranker)


def test_make_reranker_returns_none_when_keys_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("UCW_RERANK_PROVIDER", raising=False)
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", False)
    monkeypatch.setattr(rerank, "_HAS_VOYAGE", False)
    assert rerank.make_reranker() is None
    assert rerank.is_available() is False
