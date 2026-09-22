"""Rerank retrieved cards with a cross-encoder (阶段 8 · S5).

Why a second pass
-----------------
Vector search compares one embedding per document against one embedding per
query. That is fast and it is why the collection can hold thousands of
cards, but it is a *similarity of summaries*: two cards about different
topics can sit close together because they share vocabulary, and a card
that answers the question can be pushed out of the Top-3 by one that
merely mentions it.

A cross-encoder reads the query and the document *together*, which is far
more accurate and far too slow to run over a whole corpus. So the two are
used in sequence: the vector search proposes, the cross-encoder decides.

    vector search (top 10)  →  rerank  →  top 3 handed to the model

Measured on this corpus, asking about 二分查找 returned 二分查找与数值逼近
first and then 枚举与暴力 and 图的遍历 — both unrelated, both occupying
slots the model reads as context. Reranking is what removes them.

Degrading, not failing
----------------------
Every failure here returns the *unranked* cards rather than raising: the
vector results are still the right shape and still relevant, just in a
worse order. A reranker that is down should cost ranking quality, never
the whole analysis — the same contract the retriever itself keeps.

Reranking needs an endpoint that offers it. The chat-only and
embedding-only providers do not, and Ollama has no rerank route at all, so
:func:`build_reranker` returns ``None`` there and the pipeline simply
skips the second pass.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from app.config import settings
from app.rag.embedder import OPENAI_COMPATIBLE, _http_client
from app.rag.retriever import RetrievedCard

logger = logging.getLogger(__name__)

#: Maps a query and its candidate documents to one score per document.
#: Higher is more relevant; the scale is the model's own and is only ever
#: compared against other scores from the same call.
Reranker = Callable[[str, Sequence[str]], Sequence[float]]


def build_reranker() -> Reranker | None:
    """Return the configured reranker, or ``None`` when there is none.

    ``None`` means "skip the second pass", which is a normal outcome: a
    deployment using Ollama for embeddings has no rerank endpoint, and the
    pipeline is expected to keep working without one.

    Returns:
        A callable scoring documents against a query, or ``None``.
    """
    if not settings.rerank_enabled:
        logger.info("RERANK_ENABLED is off; using vector order")
        return None

    provider = (settings.embedding_provider or "").strip().lower()
    if provider != OPENAI_COMPATIBLE:
        # Ollama exposes no rerank route, and a chat-only endpoint has
        # none either. Asking would only produce a 404 dressed up as a
        # configuration error.
        logger.info(
            "rerank needs an openai_compatible endpoint (provider is %r); "
            "using vector order",
            provider,
        )
        return None

    model = (settings.rerank_model or "").strip()
    if not model:
        logger.warning("RERANK_MODEL is empty; using vector order")
        return None

    # Default to the embedding endpoint and key: a provider that serves
    # embeddings almost always serves rerank from the same place, and
    # making the common case require three more settings would mean it
    # silently never gets switched on.
    base_url = (
        (settings.rerank_base_url or "").strip()
        or (settings.embedding_base_url or "").strip()
    ).rstrip("/")
    if not base_url:
        logger.warning("no rerank endpoint configured; using vector order")
        return None

    api_key = (settings.rerank_api_key or "").strip() or (
        settings.embedding_api_key or ""
    )
    timeout = settings.embedding_timeout

    def rerank(query: str, documents: Sequence[str]) -> Sequence[float]:
        client = _http_client(base_url, api_key, timeout)
        response = client.post(
            "/rerank",
            json={
                "model": model,
                "query": query,
                "documents": list(documents),
            },
        )
        response.raise_for_status()
        rows = response.json().get("results") or []
        if len(rows) != len(documents):
            # A short or long list means the endpoint answered a different
            # question than the one asked. Scoring by position after that
            # would attach one card's score to another card.
            raise ValueError(
                f"rerank returned {len(rows)} scores for {len(documents)} documents"
            )
        scores = [0.0] * len(documents)
        for row in rows:
            scores[int(row["index"])] = float(row["relevance_score"])
        return scores

    return rerank


def rerank_cards(
    query: str,
    cards: Sequence[RetrievedCard],
    reranker: Reranker | None,
    limit: int,
) -> list[RetrievedCard]:
    """Order *cards* by relevance to *query*, keeping the best *limit*.

    Never raises, and never invents an order it cannot justify: a missing
    reranker, a failed call, or a score list of the wrong length all fall
    back to the vector order. Returning the *unranked* cards rather than an
    empty list is the important half — the candidates were relevant enough
    to be retrieved, and losing them would be a worse answer than showing
    them in a slightly wrong order.
    """
    candidates = list(cards)
    if reranker is None or not candidates:
        return candidates[:limit]

    try:
        scores = list(reranker(query, [card.text for card in candidates]))
    except Exception as exc:  # noqa: BLE001 — degradation is the contract
        logger.warning("rerank failed (%s); keeping vector order", exc)
        return candidates[:limit]

    if len(scores) != len(candidates):
        logger.warning(
            "rerank returned %d scores for %d cards; keeping vector order",
            len(scores),
            len(candidates),
        )
        return candidates[:limit]

    order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
    ranked = [candidates[i] for i in order[:limit]]
    logger.info(
        "reranked %d candidate(s) down to %d: %s",
        len(candidates),
        len(ranked),
        ", ".join(card.title for card in ranked),
    )
    return ranked


def rerank_candidate_limit() -> int:
    """How many candidates to pull before reranking.

    Must be at least the number finally shown, or the second pass has
    nothing to choose between. Clamped rather than validated so a typo in
    ``.env`` degrades to "no reranking" instead of breaking retrieval.
    """
    try:
        wanted = int(settings.rerank_candidates)
    except (TypeError, ValueError):
        return 0
    return max(0, wanted)


def describe_reranker() -> str:
    """A short description for logs and the indexing script's header."""
    if not settings.rerank_enabled:
        return "disabled"
    return f"{settings.rerank_model} (candidates {rerank_candidate_limit()})"
