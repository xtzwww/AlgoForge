"""Index the knowledge-card corpus into Milvus (阶段 8 · `AGENTS.md` S5).

:mod:`app.rag.documents` reads the cards, :mod:`app.rag.retriever` searches
for them — but until something *writes* them into a collection, the search has
nothing to find. That gap is this module.

Same construction as the retriever, for the same reason
-------------------------------------------------------
The embedder and the Milvus client are **injected**, never constructed here.
``pymilvus`` is not installed in the sidecar venv (see ``requirements.txt``),
and Milvus itself needs Docker, which this machine cannot run. Injecting both
collaborators means the whole indexing path — record building, dimension
inference, idempotent upsert — is exercised by tests with fakes, and the only
unverified surface left is the exact shape of the three Milvus calls.

What is *not* verified
----------------------
No Milvus server has ever been reachable from this repository, so
:func:`index_cards` has never run against a real instance. The call shapes
(``has_collection`` / ``create_collection`` / ``upsert``) follow the
``MilvusClient`` API but are unverified — see ``.workbuddy-ai/night-queue.md``
T7b. Do not describe this as "indexing works" until someone runs it against a
live Milvus.

Idempotence
-----------
``card_id`` is the primary key and the write is an ``upsert``, so re-indexing
the same corpus overwrites rather than duplicating. That matters because the
natural workflow is "edit a card, re-run the indexer" — with ``insert`` the
second run would leave two rows for one card, and retrieval would return the
same knowledge point twice while pushing a different one out of the Top-N.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.rag.documents import KnowledgeCard
from app.rag.schema import (
    FIELD_ERRORS,
    FIELD_ID,
    FIELD_PROBLEMS,
    FIELD_TAGS,
    FIELD_TEXT,
    FIELD_TITLE,
    FIELD_VECTOR,
    Embedder,
)

logger = logging.getLogger(__name__)


def build_records(
    cards: Sequence[KnowledgeCard],
    embedder: Embedder,
) -> list[dict[str, Any]]:
    """Embed every card and build the rows to write.

    The vector is computed from :attr:`KnowledgeCard.embed_text` (title +
    body) while the stored ``text`` is the body alone: the title travels in
    its own field, and :func:`app.rag.retriever.format_context` renders
    ``### title`` above the text. Embedding the body twice, or storing the
    title inside the text, would both put the same words in the prompt twice.

    Raises:
        ValueError: if the embedder returns vectors of differing lengths, or
            an empty vector. That means the embedder is misconfigured, and
            the failure would otherwise surface much later as a Milvus
            dimension error naming a number with no obvious origin.
    """
    records: list[dict[str, Any]] = []
    dimension: int | None = None

    for card in cards:
        vector = list(embedder(card.embed_text))
        if not vector:
            raise ValueError(f"embedder returned an empty vector for card {card.id!r}")
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise ValueError(
                f"embedder returned inconsistent dimensions: "
                f"{dimension} then {len(vector)} (card {card.id!r})"
            )

        records.append(
            {
                FIELD_ID: card.id,
                FIELD_VECTOR: vector,
                FIELD_TITLE: card.title,
                FIELD_TEXT: card.body,
                FIELD_TAGS: list(card.knowledge_tags),
                FIELD_PROBLEMS: list(card.related_problems),
                FIELD_ERRORS: list(card.common_errors),
            }
        )

    return records


def index_cards(
    client: Any,
    collection: str,
    cards: Sequence[KnowledgeCard],
    embedder: Embedder,
    *,
    recreate: bool = False,
) -> int:
    """Write *cards* into *collection*; return how many rows were written.

    The collection is created when missing, with its dimension **inferred
    from the embedder's own output** rather than hardcoded. A hardcoded
    dimension would be wrong the moment the embedding model changes, and the
    resulting failure (Milvus rejecting every insert) says nothing about the
    model being the cause.

    Args:
        recreate: Drop and rebuild the collection first. Use after changing
            the embedding model — vectors from two models are not comparable,
            so a mixed collection returns nonsense that looks like bad
            relevance rather than a configuration error.

    Returns:
        The number of rows written (0 for an empty corpus).
    """
    if not cards:
        return 0

    records = build_records(cards, embedder)
    dimension = len(records[0][FIELD_VECTOR])

    exists = client.has_collection(collection_name=collection)
    if recreate and exists:
        client.drop_collection(collection_name=collection)
        exists = False

    if not exists:
        client.create_collection(
            collection_name=collection,
            dimension=dimension,
            primary_field_name=FIELD_ID,
            vector_field_name=FIELD_VECTOR,
            auto_id=False,
        )
        logger.info("created collection %s (dimension=%d)", collection, dimension)

    # Upsert, not insert: re-indexing after editing a card must overwrite that
    # card rather than leave a second row for it (see the module docstring).
    client.upsert(collection_name=collection, data=records)
    logger.info("indexed %d card(s) into %s", len(records), collection)

    return len(records)
