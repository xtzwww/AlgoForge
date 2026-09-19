"""Tests for the knowledge retriever (阶段 8 · `AGENTS.md` S5).

The behaviour that matters most here is **not** the Milvus call — Milvus has
never been reachable from this repository, so that shape is unverified by
construction (see the module docstring of ``app/rag/retriever.py``). What is
tested is the contract around it:

* a missing vector store degrades to "no context" and **never raises**, because
  a broken knowledge base must not take error analysis down with it;
* hits are mapped onto cards correctly, including the two payload shapes
  Milvus returns across versions;
* the tag filter actually filters (an earlier draft had it silently doing
  nothing, which is why there is a test for it now);
* :func:`format_context` renders nothing — not a placeholder — when there is
  no context, so a placeholder can never reach the model as if it were
  retrieved knowledge.

Milvus itself is replaced by a stub, which is exactly why the client is
injected rather than constructed inside the retriever.
"""

from __future__ import annotations

import pytest

from app.rag.retriever import (
    DEFAULT_COLLECTION,
    MilvusRetriever,
    NullRetriever,
    RetrievedCard,
    Retriever,
    build_retriever,
    format_context,
)
from app.rag.schema import OUTPUT_FIELDS


class StubMilvus:
    """Stands in for ``pymilvus.MilvusClient``.

    Records the last call so the tests can assert *what was asked of Milvus*
    (collection, limit, requested fields) and not merely what came back.
    """

    def __init__(self, hits: object = None, error: Exception | None = None) -> None:
        self.hits = hits if hits is not None else []
        self.error = error
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.hits


def _entity_hits(
    *cards: tuple[str, str, str, list[str]],
) -> list[list[dict[str, object]]]:
    """Build Milvus's nested shape with the payload under ``entity``."""
    return [
        [
            {
                "distance": 0.9 - index * 0.1,
                "entity": {
                    "card_id": card_id,
                    "title": title,
                    "text": text,
                    "knowledge_tags": tags,
                },
            }
            for index, (card_id, title, text, tags) in enumerate(cards)
        ]
    ]


def _embedder(text: str) -> list[float]:
    return [float(len(text)), 1.0]


# ------------------------------------------------------------------ null mode


def test_null_retriever_finds_nothing() -> None:
    assert NullRetriever().search("二分怎么写") == []


def test_null_retriever_satisfies_the_protocol() -> None:
    """The graph is written against the Protocol, so the degraded
    implementation must be a drop-in."""
    assert isinstance(NullRetriever(), Retriever)


def test_build_without_an_embedder_degrades() -> None:
    """No embedder means no way to encode a query — degrade, do not raise."""
    assert isinstance(build_retriever(embedder=None), NullRetriever)


def test_build_degrades_when_pymilvus_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The realistic case on a machine without the RAG extras installed."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pymilvus":
            raise ImportError("no pymilvus")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert isinstance(build_retriever(embedder=_embedder), NullRetriever)


def test_build_degrades_when_the_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable Milvus must not propagate out of the factory."""
    import sys
    import types

    fake_module = types.ModuleType("pymilvus")

    def exploding_client(uri: str) -> object:
        raise ConnectionError(f"cannot reach {uri}")

    fake_module.MilvusClient = exploding_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymilvus", fake_module)

    retriever = build_retriever("http://nope:19530", embedder=_embedder)

    assert isinstance(retriever, NullRetriever)


def test_build_uses_the_injected_client() -> None:
    client = StubMilvus()

    retriever = build_retriever(embedder=_embedder, client=client)

    assert isinstance(retriever, MilvusRetriever)
    assert retriever.collection == DEFAULT_COLLECTION


# ------------------------------------------------------------------- mapping


def test_search_maps_hits_onto_cards() -> None:
    client = StubMilvus(_entity_hits(("binary-search", "二分查找", "正文", ["二分"])))
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    cards = retriever.search("二分")

    assert len(cards) == 1
    assert cards[0].card_id == "binary-search"
    assert cards[0].title == "二分查找"
    assert cards[0].text == "正文"
    assert cards[0].tags == ("二分",)


def test_search_asks_milvus_for_the_tags_too() -> None:
    """Without them the tag filter would have nothing to filter on."""
    client = StubMilvus(_entity_hits(("card", "标题", "正文", ["二分"])))
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    retriever.search("二分")

    assert "knowledge_tags" in client.calls[0]["output_fields"]  # type: ignore[operator]


def test_search_requests_exactly_the_shared_output_fields() -> None:
    """Pins the indexer/retriever agreement.

    These two drifted once already: the indexer declared field-name constants
    precisely so the pair could not disagree, while the retriever kept passing
    its own hardcoded list. A field the retriever asks for but the indexer
    never writes raises nothing — Milvus returns empty for it, and retrieval
    quietly degrades to no context.
    """
    client = StubMilvus(_entity_hits(("card", "标题", "正文", [])))
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    retriever.search("任意")

    assert client.calls[0]["output_fields"] == list(OUTPUT_FIELDS)  # type: ignore[operator]


def test_search_accepts_a_flat_payload() -> None:
    """Older/newer Milvus versions nest the payload differently; both must work."""
    client = StubMilvus(
        [[{"distance": 0.5, "card_id": "flat", "title": "平铺", "text": "正文"}]]
    )
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    cards = retriever.search("任意")

    assert [card.card_id for card in cards] == ["flat"]


def test_search_skips_hits_without_an_id() -> None:
    client = StubMilvus([[{"distance": 0.5, "entity": {"title": "没有 id"}}]])
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    assert retriever.search("任意") == []


# ---------------------------------------------------------------- degradation


def test_search_returns_nothing_when_milvus_raises() -> None:
    """The whole point: a broken knowledge base must not break the diagnosis."""
    client = StubMilvus(error=RuntimeError("milvus is down"))
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    assert retriever.search("二分") == []


def test_search_returns_nothing_when_the_embedder_raises() -> None:
    def bad_embedder(text: str) -> list[float]:
        raise ValueError("embedding service unavailable")

    client = StubMilvus(_entity_hits(("card", "标题", "正文", [])))
    retriever = MilvusRetriever(client=client, collection="c", embedder=bad_embedder)

    assert retriever.search("二分") == []


# --------------------------------------------------------------- tag filter


def test_tag_filter_keeps_matching_cards() -> None:
    client = StubMilvus(
        _entity_hits(
            ("dp", "动态规划", "正文", ["动态规划"]),
            ("io", "输入输出", "正文", ["输入输出"]),
        )
    )
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    cards = retriever.search("怎么写", tags=["动态规划"])

    assert [card.card_id for card in cards] == ["dp"]


def test_tag_filter_falls_back_when_nothing_matches() -> None:
    """A filter that matches nothing is more likely a corpus gap than proof
    that the retrieved cards are irrelevant — so the hits are kept."""
    client = StubMilvus(_entity_hits(("dp", "动态规划", "正文", ["动态规划"])))
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    cards = retriever.search("怎么写", tags=["题库里没有的标签"])

    assert [card.card_id for card in cards] == ["dp"]


def test_search_honours_the_limit() -> None:
    client = StubMilvus(
        _entity_hits(
            ("a", "A", "正文", []),
            ("b", "B", "正文", []),
            ("c", "C", "正文", []),
        )
    )
    retriever = MilvusRetriever(client=client, collection="c", embedder=_embedder)

    assert len(retriever.search("任意", limit=2)) == 2


# ------------------------------------------------------------------- context


def test_format_context_is_empty_without_cards() -> None:
    """No placeholder: an invented "（无相关知识）" would be pasted into the
    prompt as if it were retrieved knowledge."""
    assert format_context([]) == ""


def test_format_context_includes_titles_and_bodies() -> None:
    cards = [
        RetrievedCard(card_id="a", title="二分查找", text="正文 A", score=0.9),
        RetrievedCard(card_id="b", title="贪心", text="正文 B", score=0.8),
    ]

    rendered = format_context(cards)

    assert "二分查找" in rendered
    assert "正文 A" in rendered
    assert "贪心" in rendered
    assert "正文 B" in rendered
