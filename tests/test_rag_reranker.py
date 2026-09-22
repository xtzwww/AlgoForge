"""The second retrieval pass: propose with vectors, decide with a cross-encoder.

Vector search compares summaries, so two cards about different topics can
sit close together because they share vocabulary. Measured on this corpus,
asking about 二分查找 put 枚举与暴力 and 图的遍历 in the Top-3 — both
unrelated, both read by the model as context. These tests pin the ranking
behaviour and, more importantly, the *degradation*: a reranker that fails
must cost ordering quality and nothing else.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.rag import build_reranker
from app.rag import reranker as reranker_module
from app.rag.reranker import rerank_candidate_limit, rerank_cards
from app.rag.retriever import RetrievedCard


def card(name: str) -> RetrievedCard:
    return RetrievedCard(
        card_id=name.lower(),
        title=name,
        text=f"{name} 的正文",
        score=0.5,
        tags=(),
    )


CARDS = [card("二分查找"), card("动态规划"), card("图的遍历")]


def test_rerank_reorders_and_truncates():
    def reranker(query, documents):
        return [0.1, 0.9, 0.5]

    ranked = rerank_cards("二分查找", CARDS, reranker, 2)

    assert [c.title for c in ranked] == ["动态规划", "图的遍历"]


def test_failed_rerank_keeps_the_vector_order():
    """Losing the reranker must cost ordering, not the candidates.

    The cards were relevant enough to be retrieved; dropping them would be
    a worse answer than showing them in a slightly wrong order.
    """

    def broken(query, documents):
        raise RuntimeError("rerank endpoint is down")

    ranked = rerank_cards("二分查找", CARDS, broken, 3)

    assert [c.title for c in ranked] == ["二分查找", "动态规划", "图的遍历"]


def test_score_list_of_the_wrong_length_is_refused():
    """Scoring by position after a short list attaches one card's score to another."""

    def short(query, documents):
        return [0.9]

    ranked = rerank_cards("二分查找", CARDS, short, 3)

    assert [c.title for c in ranked] == ["二分查找", "动态规划", "图的遍历"]


def test_without_a_reranker_the_vector_order_stands():
    ranked = rerank_cards("q", CARDS, None, 2)

    assert [c.title for c in ranked] == ["二分查找", "动态规划"]


def test_rerank_of_nothing_is_nothing():
    assert rerank_cards("q", [], lambda q, d: [], 3) == []


def test_ollama_provider_has_no_rerank_route(monkeypatch, caplog):
    """Ollama exposes no /rerank, so asking would be a 404 in disguise."""
    monkeypatch.setattr(settings, "rerank_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "ollama")

    with caplog.at_level(logging.INFO):
        assert build_reranker() is None
    assert "needs an openai_compatible endpoint" in caplog.text


def test_rerank_can_be_switched_off(monkeypatch, caplog):
    monkeypatch.setattr(settings, "rerank_enabled", False)

    with caplog.at_level(logging.INFO):
        assert build_reranker() is None
    assert "RERANK_ENABLED is off" in caplog.text


def test_candidate_limit_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(settings, "rerank_candidates", "not a number")
    assert rerank_candidate_limit() == 0

    monkeypatch.setattr(settings, "rerank_candidates", -5)
    assert rerank_candidate_limit() == 0


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple] = []

    def post(self, path, json=None):
        self.calls.append((path, json))
        return _FakeResponse(self.payload)


def test_rerank_call_shape(monkeypatch):
    """The request must carry the query and every candidate document."""
    client = _FakeClient(
        {
            "results": [
                {"index": 1, "relevance_score": 0.8},
                {"index": 0, "relevance_score": 0.2},
            ]
        }
    )
    # Patched on *this* module: reranker.py bound the name at import time,
    # so patching app.rag.embedder would leave this call site untouched.
    monkeypatch.setattr(reranker_module, "_http_client", lambda *a: client)
    monkeypatch.setattr(settings, "rerank_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "rerank_base_url", "")
    monkeypatch.setattr(settings, "rerank_model", "BAAI/bge-reranker-v2-m3")

    reranker = build_reranker()
    assert reranker is not None
    # Scores come back in the order asked, not the order answered.
    assert list(reranker("二分查找", ["d1", "d2"])) == [0.2, 0.8]

    path, payload = client.calls[0]
    assert path == "/rerank"
    assert payload["model"] == "BAAI/bge-reranker-v2-m3"
    assert payload["query"] == "二分查找"
    assert payload["documents"] == ["d1", "d2"]


def test_rerank_falls_back_to_the_embedding_endpoint(monkeypatch):
    """A provider that serves embeddings serves rerank from the same place."""
    monkeypatch.setattr(settings, "rerank_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "rerank_base_url", "")

    assert build_reranker() is not None


def test_a_rerank_endpoint_with_no_model_is_refused(monkeypatch, caplog):
    monkeypatch.setattr(settings, "rerank_enabled", True)
    monkeypatch.setattr(settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "rerank_model", "")

    with caplog.at_level(logging.WARNING):
        assert build_reranker() is None
    assert "RERANK_MODEL is empty" in caplog.text
