"""Tests for the knowledge-card indexer (阶段 8 · `AGENTS.md` S5).

Milvus is replaced by a stub, which is the whole reason the client is injected
rather than constructed inside :func:`index_cards`. The call shapes against a
real server remain unverified — no Milvus has ever been reachable from this
repository — so what is pinned here is everything *around* the calls:

* what gets embedded versus what gets stored (title in its own field, body in
  ``text``, never both in both);
* the dimension is inferred from the embedder, never hardcoded;
* the collection is only created when missing, and ``recreate`` actually
  rebuilds it;
* writes are ``upsert``, so re-indexing after an edit cannot leave two rows
  for one card;
* a misconfigured embedder fails loudly at indexing time instead of surfacing
  later as a Milvus dimension error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.documents import KnowledgeCard, load_knowledge_cards
from app.rag.indexer import build_records, index_cards
from app.rag.schema import (
    FIELD_ERRORS,
    FIELD_ID,
    FIELD_PROBLEMS,
    FIELD_TAGS,
    FIELD_TEXT,
    FIELD_TITLE,
    FIELD_VECTOR,
    MAX_ID_LENGTH,
    OUTPUT_FIELDS,
)


class StubMilvus:
    """Stands in for ``pymilvus.MilvusClient``.

    Records every call so the tests can assert *what was asked of Milvus* —
    a stub that only returns canned data would let a wrong call shape pass.
    """

    def __init__(self, existing: bool = False) -> None:
        self.existing = existing
        self.created: list[dict[str, object]] = []
        self.dropped: list[str] = []
        self.upserted: list[dict[str, object]] = []

    def has_collection(self, collection_name: str) -> bool:
        return self.existing

    def create_collection(self, **kwargs: object) -> None:
        self.created.append(kwargs)
        self.existing = True

    def drop_collection(self, collection_name: str) -> None:
        self.dropped.append(collection_name)
        self.existing = False

    def upsert(self, **kwargs: object) -> None:
        self.upserted.append(kwargs)


def _card(card_id: str = "sample", body: str = "## 要点\n\n正文。") -> KnowledgeCard:
    return KnowledgeCard(
        id=card_id,
        title="示例卡片",
        knowledge_tags=("二分",),
        related_problems=("p011-binary-search",),
        common_errors=("off_by_one",),
        body=body,
        source=Path(f"{card_id}.md"),
    )


def _embedder(text: str) -> list[float]:
    """Deterministic, and its length depends on nothing but the input."""
    return [float(len(text)), 1.0, 0.0]


# ------------------------------------------------------------------- records


def test_build_records_embeds_the_title_plus_body() -> None:
    """The vector must be built from ``embed_text`` — the title is what a
    student would search for, so leaving it out of the embedding costs recall."""
    card = _card()

    record = build_records([card], _embedder)[0]

    assert record[FIELD_VECTOR] == _embedder(card.embed_text)


def test_build_records_stores_the_body_not_the_embed_text() -> None:
    """The title has its own field and ``format_context`` renders it above the
    text — storing it inside ``text`` too would put it in the prompt twice."""
    card = _card()

    record = build_records([card], _embedder)[0]

    assert record[FIELD_TEXT] == card.body
    assert card.title not in record[FIELD_TEXT]
    assert record[FIELD_TITLE] == card.title


def test_build_records_carries_the_metadata() -> None:
    """The retriever filters on tags and the teacher-facing UI cites the
    problem — both need these fields present in the collection."""
    record = build_records([_card()], _embedder)[0]

    assert record[FIELD_ID] == "sample"
    assert record[FIELD_TAGS] == ["二分"]
    assert record[FIELD_PROBLEMS] == ["p011-binary-search"]
    assert record[FIELD_ERRORS] == ["off_by_one"]


def test_build_records_rejects_an_empty_vector() -> None:
    def empty_embedder(text: str) -> list[float]:
        return []

    with pytest.raises(ValueError, match="empty vector"):
        build_records([_card()], empty_embedder)


def test_build_records_rejects_inconsistent_dimensions() -> None:
    """A dimension change mid-corpus means the embedder is misconfigured.
    Catching it here names the card; Milvus would only report a number."""
    calls = {"n": 0}

    def drifting_embedder(text: str) -> list[float]:
        calls["n"] += 1
        return [0.0] * (3 if calls["n"] == 1 else 4)

    with pytest.raises(ValueError, match="inconsistent dimensions"):
        build_records([_card("a"), _card("b")], drifting_embedder)


def test_build_records_of_an_empty_corpus_is_empty() -> None:
    assert build_records([], _embedder) == []


# ---------------------------------------------------------------- indexing


def test_indexing_an_empty_corpus_touches_nothing() -> None:
    client = StubMilvus()

    assert index_cards(client, "c", [], _embedder) == 0
    assert client.created == []
    assert client.upserted == []


def test_index_creates_the_collection_with_the_inferred_dimension() -> None:
    """Hardcoding 768 or 1024 would silently break the moment the embedding
    model changes, and the resulting error would not name the model."""
    client = StubMilvus(existing=False)
    card = _card()

    index_cards(client, "cards", [card], _embedder)

    assert len(client.created) == 1
    assert client.created[0]["dimension"] == len(_embedder(card.embed_text))
    assert client.created[0]["primary_field_name"] == FIELD_ID
    assert client.created[0]["vector_field_name"] == FIELD_VECTOR
    assert client.created[0]["auto_id"] is False


def test_the_primary_key_is_created_as_a_string() -> None:
    """The regression guard for a bug no fake could catch.

    ``MilvusClient.create_collection`` defaults ``id_type`` to ``"int"``,
    so the primary key is typed INT64 — and every upsert of a ``card_id``
    like ``"binary-search"`` then dies with ``DataNotMatchException:
    {card_id} field should be a int64, but got a {<class 'str'>}``.
    ``max_length`` alone does nothing; it is only read once the key is
    already a varchar.

    Found 2026-09-20 by running against a real Milvus for the first time
    (``tests/test_rag_milvus_live.py``). The stub here recorded the call
    and stayed green, which is exactly why this assertion is now explicit:
    a fake cannot tell ``id_type="int"`` from ``id_type="string"``.
    """
    client = StubMilvus(existing=False)

    index_cards(client, "cards", [_card()], _embedder)

    assert client.created[0]["id_type"] == "string"
    assert client.created[0]["max_length"] == MAX_ID_LENGTH


def test_index_reuses_an_existing_collection() -> None:
    """Re-indexing must not try to create the collection again."""
    client = StubMilvus(existing=True)

    index_cards(client, "cards", [_card()], _embedder)

    assert client.created == []
    assert client.dropped == []


def test_recreate_drops_and_rebuilds() -> None:
    """Required after changing the embedding model: vectors from two models
    are not comparable, and a mixed collection returns nonsense that reads as
    bad relevance rather than as a configuration error."""
    client = StubMilvus(existing=True)

    index_cards(client, "cards", [_card()], _embedder, recreate=True)

    assert client.dropped == ["cards"]
    assert len(client.created) == 1


def test_recreate_on_a_missing_collection_does_not_drop() -> None:
    client = StubMilvus(existing=False)

    index_cards(client, "cards", [_card()], _embedder, recreate=True)

    assert client.dropped == []
    assert len(client.created) == 1


def test_index_upserts_rather_than_inserts() -> None:
    """Re-indexing an edited card must overwrite it. With ``insert`` the
    second run would leave two rows for one card, and retrieval would return
    the same knowledge point twice while pushing another out of the Top-N."""
    client = StubMilvus()

    index_cards(client, "cards", [_card()], _embedder)

    assert len(client.upserted) == 1
    assert not hasattr(client, "inserted")


def test_index_writes_every_card_in_one_call() -> None:
    client = StubMilvus()
    cards = [_card("a"), _card("b"), _card("c")]

    written = index_cards(client, "cards", cards, _embedder)

    assert written == 3
    assert len(client.upserted) == 1
    assert len(client.upserted[0]["data"]) == 3  # type: ignore[arg-type]


def test_index_returns_the_number_written() -> None:
    client = StubMilvus()

    assert index_cards(client, "cards", [_card()], _embedder) == 1


# ------------------------------------------------------------- real corpus


def test_the_shipped_corpus_indexes_as_one_batch() -> None:
    """End-to-end over the real cards, with a fake Milvus and a fake embedder.

    This is the closest thing to an integration test available: it proves the
    loader and the indexer agree on the data, which no amount of stubbing each
    in isolation would catch.
    """
    client = StubMilvus()
    cards = load_knowledge_cards()

    written = index_cards(client, "cards", cards, _embedder)

    assert written == len(cards)
    rows = client.upserted[0]["data"]
    assert len(rows) == len(cards)  # type: ignore[arg-type]
    assert all(row[FIELD_TEXT].strip() for row in rows)  # type: ignore[union-attr]


def test_retriever_output_fields_are_all_written_by_the_indexer() -> None:
    """The two modules must agree on field names: a field the retriever asks
    for but the indexer never writes shows up as silently empty context."""
    written_fields = set(build_records([_card()], _embedder)[0])

    assert set(OUTPUT_FIELDS) <= written_fields
