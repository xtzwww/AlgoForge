"""The default ``direct`` pipeline must actually consult the knowledge base.

Until 2026-09-21 the retrieval layer was built and tested but unreachable
from a real submission: ``build_retriever`` was always handed
``embedder=None``, degraded to :class:`NullRetriever`, and the analysis
went to the model with no reference material. Nothing failed and nothing
was retrieved — the quietest possible way for a feature to be missing.

These tests pin the wiring, not the vector maths: the embedder and the
Milvus client are replaced, and what is asserted is that a submission
reaches the model *carrying* the cards, that a retrieval failure costs the
context but not the submission, and that switching retrieval off is still
honoured.
"""

from __future__ import annotations

import logging

import pytest

from app.agent import pipeline
from app.agent.analyzer import AnalysisOutcome
from app.config import settings
from app.rag import build_embedder, resolve_milvus_uri
from app.rag import embedder as embedder_module
from app.rag.retriever import RetrievedCard

CARD = RetrievedCard(
    card_id="binary-search",
    title="二分查找与数值逼近",
    text="写二分不要靠记忆模板，靠想清楚循环不变量。",
    score=0.65,
    tags=("binary_search",),
)


class _FakeRetriever:
    """Stands in for Milvus: returns fixed cards, or blows up."""

    def __init__(self, cards=None, raises: bool = False) -> None:
        self.cards = cards or []
        self.raises = raises
        self.queries: list[str] = []

    def search(self, query, tags=(), limit=3):
        self.queries.append(query)
        if self.raises:
            raise RuntimeError("milvus exploded")
        return self.cards


def _install_retriever(monkeypatch, retriever):
    """Point the pipeline's lazy RAG imports at *retriever*."""
    import app.rag

    monkeypatch.setattr(app.rag, "build_embedder", lambda: (lambda text: [0.1, 0.2]))
    monkeypatch.setattr(app.rag, "build_retriever", lambda *a, **k: retriever)


def _capture_analysis(monkeypatch):
    """Replace the model call; return the kwargs it would have received."""
    captured: dict = {}

    def fake_analyze_error(*args, **kwargs):
        captured.update(kwargs)
        return AnalysisOutcome(error="stopped before the model")

    monkeypatch.setattr(pipeline, "analyze_error", fake_analyze_error)
    return captured


@pytest.fixture(autouse=True)
def _direct_pipeline(monkeypatch):
    monkeypatch.setattr(settings, "analysis_pipeline", "direct")
    monkeypatch.setattr(settings, "rag_enabled", True)


def test_direct_path_passes_retrieved_cards_to_the_model(monkeypatch):
    retriever = _FakeRetriever(cards=[CARD])
    _install_retriever(monkeypatch, retriever)
    captured = _capture_analysis(monkeypatch)

    pipeline.analyse(
        code="int main(){return 0;}",
        stdin="5",
        expected="3",
        actual="-1",
        statement="用二分查找找出目标值第一次出现的位置",
        verdict="WA",
    )

    context = captured.get("extra_context") or ""
    assert CARD.title in context
    assert CARD.text in context


def test_query_prefers_the_statement_over_the_code(monkeypatch):
    """Searching with buggy source retrieves cards about the mistake.

    Working out that mistake is the model's job, so feeding the code back
    would bias retrieval toward whatever the code looks like it is doing.
    """
    retriever = _FakeRetriever(cards=[CARD])
    _install_retriever(monkeypatch, retriever)
    _capture_analysis(monkeypatch)

    pipeline.analyse(
        code="SOURCE-SHOULD-NOT-BE-THE-QUERY",
        stdin="",
        expected="",
        actual="",
        statement="动态规划的状态转移方程怎么写",
        verdict="WA",
    )

    assert retriever.queries == ["动态规划的状态转移方程怎么写"]


def test_retrieval_failure_costs_context_not_the_submission(monkeypatch):
    _install_retriever(monkeypatch, _FakeRetriever(raises=True))
    captured = _capture_analysis(monkeypatch)

    pipeline.analyse(
        code="int main(){}",
        stdin="",
        expected="",
        actual="",
        statement="二分查找",
        verdict="WA",
    )

    assert (captured.get("extra_context") or "") == ""


def test_retrieval_disabled_skips_the_rag_stack(monkeypatch):
    monkeypatch.setattr(settings, "rag_enabled", False)
    called = []
    import app.rag

    monkeypatch.setattr(app.rag, "build_embedder", lambda: called.append(True))
    captured = _capture_analysis(monkeypatch)

    pipeline.analyse(
        code="int main(){}",
        stdin="",
        expected="",
        actual="",
        statement="二分查找",
        verdict="WA",
    )

    assert called == []
    assert (captured.get("extra_context") or "") == ""


def test_explicit_empty_context_is_not_overwritten(monkeypatch):
    """A caller passing ``""`` means "no context", not "guess for me"."""
    _install_retriever(monkeypatch, _FakeRetriever(cards=[CARD]))
    captured = _capture_analysis(monkeypatch)

    pipeline.analyse(
        code="int main(){}",
        stdin="",
        expected="",
        actual="",
        statement="二分查找",
        verdict="WA",
        extra_context="",
    )

    assert captured.get("extra_context") == ""


def test_resolve_milvus_uri_prefers_the_embedded_file(monkeypatch):
    monkeypatch.setattr(settings, "milvus_lite_path", "D:/x/milvus.db")
    monkeypatch.setattr(settings, "milvus_uri", "http://localhost:19530")
    assert resolve_milvus_uri() == "D:/x/milvus.db"

    monkeypatch.setattr(settings, "milvus_lite_path", "")
    assert resolve_milvus_uri() == "http://localhost:19530"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []

    def post(self, path, json=None):
        self.calls.append((path, json))
        return _FakeResponse({"embedding": [0.1, 0.2, 0.3]})


def test_ollama_embedder_sends_prompt_and_keeps_the_model_warm(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(embedder_module, "_http_client", lambda *a: client)
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    monkeypatch.setattr(settings, "embedding_model", "bge-m3")
    monkeypatch.setattr(settings, "embedding_keep_alive", "30m")

    embed = build_embedder()
    assert embed is not None
    assert list(embed("二分查找")) == [0.1, 0.2, 0.3]

    path, payload = client.calls[0]
    assert path == "/api/embeddings"
    assert payload["model"] == "bge-m3"
    assert payload["prompt"] == "二分查找"
    # Without keep_alive the model is unloaded after five minutes and the
    # next student pays the whole load again (52s for bge-m3, measured).
    assert payload["keep_alive"] == "30m"


def test_no_embedding_provider_means_no_embedder(monkeypatch, caplog):
    monkeypatch.setattr(settings, "embedding_provider", "none")
    with caplog.at_level(logging.INFO):
        assert build_embedder() is None


def test_unknown_provider_is_reported_not_guessed(monkeypatch, caplog):
    monkeypatch.setattr(settings, "embedding_provider", "definitely-not-a-provider")
    with caplog.at_level(logging.WARNING):
        assert build_embedder() is None
    assert "unknown EMBEDDING_PROVIDER" in caplog.text


def test_missing_model_name_disables_retrieval(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    monkeypatch.setattr(settings, "embedding_model", "")
    assert build_embedder() is None


def test_ensure_loaded_tolerates_a_client_without_preloading():
    """The test doubles answer no Milvus calls; preloading must not break."""
    from app.rag.retriever import _ensure_loaded

    class _DumbClient:
        def has_collection(self, collection_name):
            raise AttributeError("not a real client")

    _ensure_loaded(_DumbClient(), "whatever")
