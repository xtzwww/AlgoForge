"""Retrieval over the knowledge-card corpus (阶段 8 · `AGENTS.md` S5).

Why this module is built to *degrade* rather than to require Milvus
------------------------------------------------------------------
Milvus is the locked vector store (``AGENTS.md`` §二.2 — no Chroma, no
sqlite-vec), and it needs Docker to run. The development machine cannot run
Docker at all, and a deployed machine may simply not have Milvus up.

If retrieval were a hard dependency, a missing vector store would take down
**error analysis** — the one feature the entire system exists to provide. That
trade is not worth making: retrieval improves a diagnosis, but a diagnosis
without retrieval is still useful. So the contract here is that building a
retriever never raises, and a retriever that cannot reach Milvus returns no
results instead of propagating the failure.

That decision is why the three pieces below exist:

* :class:`Retriever` — what the analysis graph actually needs, so the graph
  does not have to know whether Milvus is up.
* :class:`NullRetriever` — the honest "I have no context to offer" answer.
* :class:`MilvusRetriever` — the real one, taking its client and embedder as
  **injected collaborators**.

Why the client and embedder are injected
----------------------------------------
``pymilvus`` is deliberately **not** imported at module scope: it is not
installed in the sidecar venv today, and a module that cannot be imported
without an optional dependency cannot be unit-tested either. Injecting the
two collaborators keeps the unverifiable surface down to a single thin
factory (:func:`build_retriever`) plus the exact Milvus call shape inside
:meth:`MilvusRetriever.search`.

What is *not* verified
----------------------
Milvus has never been reachable from this repository, so
:meth:`MilvusRetriever.search` has never executed against a real server.
Treat its Milvus call shape as unverified until someone runs it against a
live instance — see ``.workbuddy-ai/night-queue.md`` T7.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.rag.schema import OUTPUT_FIELDS, Embedder

logger = logging.getLogger(__name__)

#: Collection holding the embedded knowledge cards. A constant rather than a
#: setting: the indexer and the retriever must agree on it, and a mismatch
#: would show up as "retrieval returns nothing" with no error at all.
DEFAULT_COLLECTION = "algoforge_knowledge_cards"

#: Default number of cards handed to the model. ``AGENTS.md`` §三 S5 sets the
#: acceptance bar at "Top-3 contains at least one relevant card".
DEFAULT_LIMIT = 3


@dataclass(frozen=True)
class RetrievedCard:
    """One card returned by a search, with its similarity score.

    Attributes:
        card_id: The card's id, i.e. its Milvus primary key.
        title: Card title, for citing the source back to the student.
        text: The card body (or the matching chunk of it).
        tags: The card's knowledge tags, carried through so a tag filter can
            be applied to the results. Without them a "filter" would have
            nothing to filter on and would silently become a no-op.
        score: Similarity, higher is closer. Not comparable across backends —
            it exists so callers can rank or threshold, not so it can be shown
            to a student as a confidence number.
    """

    card_id: str
    title: str
    text: str
    score: float
    tags: tuple[str, ...] = ()


@runtime_checkable
class Retriever(Protocol):
    """What the analysis graph needs from a knowledge source.

    Deliberately one method. The graph wants "give me context for this
    question"; anything else (indexing, upserting, collection management) is
    the indexer's business, and folding it in here would force every fake
    retriever in the tests to implement operations it never uses.
    """

    def search(
        self,
        query: str,
        tags: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
    ) -> list[RetrievedCard]:
        """Return up to *limit* cards relevant to *query*.

        *tags* are the knowledge tags of the problem being diagnosed. They are
        a filter hint, not a requirement: a backend that cannot filter may
        ignore them rather than return nothing.
        """
        ...


class NullRetriever:
    """A retriever that always finds nothing.

    This is the degraded mode, and it is a first-class outcome rather than an
    error path: with no Milvus the system still analyses submissions, it just
    does so without knowledge-base context. Callers must therefore treat an
    empty result as normal — never as "the student has no related knowledge".

    It is a class (not ``None``) so callers need no ``if retriever is not
    None`` branch, and so the degraded path is exercised by the same code as
    the real one.
    """

    def search(
        self,
        query: str,
        tags: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
    ) -> list[RetrievedCard]:
        return []


class MilvusRetriever:
    """Retrieval backed by a Milvus collection of embedded cards.

    The client and embedder are injected so this class can be tested with
    fakes, and so importing this module never requires ``pymilvus``.

    Attributes:
        client: Anything exposing Milvus's ``search`` method. Kept structural
            (not typed against ``pymilvus.MilvusClient``) precisely so the
            tests can pass a stub.
        collection: Collection name to search.
        embedder: Maps a query string to a vector, in the same space the
            indexer embedded the cards with. Getting this wrong is silent —
            the search simply returns irrelevant cards — so the indexer and
            the retriever must be configured with the *same* model.
    """

    def __init__(
        self,
        client: Any,
        collection: str,
        embedder: Embedder,
    ) -> None:
        self.client = client
        self.collection = collection
        self.embedder = embedder

    def search(
        self,
        query: str,
        tags: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
    ) -> list[RetrievedCard]:
        """Embed *query*, search Milvus, and map hits onto cards.

        Tag filtering is applied **after** the vector search rather than
        pushed into Milvus's filter expression. That is a deliberate,
        temporary simplification: Milvus's array-filter syntax varies by
        version, and guessing at it in code that cannot be run against a
        server would be unverifiable. The cost is that a tag filter can
        return fewer than *limit* cards — acceptable for prompt context.
        Revisit once a live Milvus is available to test the expression
        against (see the module docstring).

        Never raises: a failure here means "no context", not "no diagnosis".
        """
        try:
            vector = list(self.embedder(query))
            hits = self.client.search(
                collection_name=self.collection,
                data=[vector],
                limit=limit,
                output_fields=list(OUTPUT_FIELDS),
            )
        except Exception as exc:  # noqa: BLE001 — degradation is the contract
            logger.warning(
                "knowledge retrieval failed, continuing without context: %s", exc
            )
            return []

        cards = self._map_hits(hits)
        if tags:
            wanted = set(tags)
            filtered = [card for card in cards if wanted.intersection(card.tags)]
            if filtered:
                return filtered[:limit]
            # A tag filter that matches nothing is more likely a corpus gap
            # than a signal that the cards are irrelevant, so fall back to the
            # unfiltered hits rather than returning an empty context.
        return cards[:limit]

    @staticmethod
    def _map_hits(hits: Any) -> list[RetrievedCard]:
        """Flatten Milvus's nested result shape into cards.

        Milvus returns a list per query vector, each holding dicts that may
        carry the payload either at the top level or under ``entity``
        depending on version. Both are accepted so a version difference
        degrades to "fewer fields" instead of an exception.
        """
        cards: list[RetrievedCard] = []
        for group in hits or []:
            for hit in group or []:
                if not isinstance(hit, dict):
                    continue
                entity = hit.get("entity", hit)
                card_id = entity.get("card_id") or hit.get("id")
                if not card_id:
                    continue
                cards.append(
                    RetrievedCard(
                        card_id=str(card_id),
                        title=str(entity.get("title", "")),
                        text=str(entity.get("text", "")),
                        score=float(hit.get("distance", 0.0)),
                        tags=tuple(
                            str(tag) for tag in (entity.get("knowledge_tags") or ())
                        ),
                    )
                )
        return cards


def build_retriever(
    milvus_uri: str | None = None,
    *,
    collection: str = DEFAULT_COLLECTION,
    embedder: Embedder | None = None,
    client: Any | None = None,
) -> Retriever:
    """Return the best retriever available, never raising.

    Order of preference:

    1. *client* if given — the tests' entry point, and how a caller that
       already holds a connection avoids opening a second one.
    2. A Milvus client built from *milvus_uri*, if ``pymilvus`` is importable
       and the connection succeeds.
    3. :class:`NullRetriever` — the honest degraded answer.

    Steps 2 and 3 both log why, because "retrieval silently returns nothing"
    is the kind of failure that gets misread as "the knowledge base has no
    relevant content".
    """
    if client is not None:
        if embedder is None:
            logger.warning("no embedder supplied; retrieval disabled")
            return NullRetriever()
        return MilvusRetriever(client=client, collection=collection, embedder=embedder)

    if embedder is None:
        logger.info("no embedder configured; running without knowledge retrieval")
        return NullRetriever()

    try:
        from pymilvus import MilvusClient  # noqa: PLC0415 — optional dependency
    except ImportError:
        logger.info("pymilvus is not installed; running without knowledge retrieval")
        return NullRetriever()

    uri = milvus_uri or "http://localhost:19530"
    try:
        return MilvusRetriever(
            client=MilvusClient(uri=uri),
            collection=collection,
            embedder=embedder,
        )
    except Exception as exc:  # noqa: BLE001 — degradation is the contract
        logger.warning(
            "could not connect to Milvus at %s (%s); running without retrieval",
            uri,
            exc,
        )
        return NullRetriever()


def format_context(cards: Sequence[RetrievedCard]) -> str:
    """Render retrieved cards into a prompt block, or ``""`` when there are none.

    Returning an empty string rather than a placeholder is deliberate: the
    prompt template decides what "no context" looks like, and a placeholder
    invented here would end up pasted into the model's input as if it were
    retrieved knowledge.
    """
    if not cards:
        return ""

    blocks = [f"### {card.title}\n{card.text}".strip() for card in cards]
    return "\n\n".join(blocks)
