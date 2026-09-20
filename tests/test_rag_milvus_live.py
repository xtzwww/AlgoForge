"""Index and search the real corpus against a real Milvus engine (S5).

Everything else in ``tests/test_rag_*`` runs against fakes, which is the
right way to test the *logic* — and the reason the shape of the Milvus
calls went unverified for so long. This file closes that gap by running
``app.rag.indexer.index_cards`` and ``app.rag.retriever.MilvusRetriever``
against ``milvus-lite``: Milvus's embedded build, which needs no Docker
and runs on Windows.

That distinction matters when reporting what has been proven:

* ✅ **a real Milvus engine** — same code path, same client, real schema,
  real vectors, real search. This is what caught the primary-key bug.
* ⬜ **a Milvus service** — standalone/cluster over ``MILVUS_URI``. Not
  exercised here, and the parts most likely to differ (auth, index
  selection at scale, consistency) are exactly what Lite does not do.

Opting in
---------
``pytest.importorskip("milvus_lite")``: the suite stays green on a machine
without it, and ``milvus-lite`` is **not** a project dependency — it pulls
``faiss-cpu`` and ``pyarrow``, which the packaged sidecar must not carry
(see the ``excludes`` in ``sidecar/packaging/algoforge-sidecar.spec``).
Install it only to run this file::

    sidecar/.venv/Scripts/python.exe -m pip install milvus-lite

The embedder
------------
Hashed character bigrams, L2-normalised. Chosen because it is deterministic
and needs no model: the question under test is whether *the retrieval
plumbing* returns the right card, not whether a particular embedding model
captures semantics. Lexical overlap is enough to answer that, and a real
model would make this file depend on a network call.
"""

from __future__ import annotations

import hashlib
import math
import shutil
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("milvus_lite", reason="milvus-lite is not installed")

from pymilvus import MilvusClient  # noqa: E402

from app.agent.graph import build_analysis_graph  # noqa: E402
from app.rag.documents import load_knowledge_cards  # noqa: E402
from app.rag.indexer import index_cards  # noqa: E402
from app.rag.retriever import MilvusRetriever  # noqa: E402

DIM = 64
COLLECTION = "knowledge_cards"

#: ASCII-only: the project's compiler fixtures already had to move here for
#: the same reason (see ``conftest.ascii_tmp_path``).
_TMP_ROOT = Path(r"C:\Windows\Temp\algoforge_milvus_live")


def _embed(text: str) -> list[float]:
    """Hashed character bigrams, L2-normalised. See the module docstring."""
    vector = [0.0] * DIM
    packed = "".join(ch for ch in text if not ch.isspace())
    grams = [packed[i : i + 2] for i in range(len(packed) - 1)]
    if not grams and packed:
        grams = [packed]
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=4).hexdigest()
        vector[int(digest, 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture(scope="module")
def cards() -> list[Any]:
    return load_knowledge_cards()


@pytest.fixture(scope="module")
def milvus() -> Any:
    """A Milvus Lite instance holding the indexed corpus."""
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    db_dir = _TMP_ROOT / "live"
    shutil.rmtree(db_dir, ignore_errors=True)
    db_dir.mkdir(parents=True)

    client = MilvusClient(uri=str(db_dir / "milvus.db"))
    yield client
    client.close()


@pytest.fixture(scope="module")
def indexed(milvus: Any, cards: list[Any]) -> Any:
    assert index_cards(milvus, COLLECTION, cards, _embed) == len(cards)
    return milvus


# ------------------------------------------------------------------- indexing


def test_the_collection_accepts_string_card_ids(
    indexed: Any, cards: list[Any]
) -> None:
    """The bug this file was written to catch.

    ``create_collection`` defaults ``id_type`` to ``"int"``, so Milvus types
    the primary key as INT64 — and every upsert then fails with
    ``DataNotMatchException: {card_id} field should be a int64, but got a
    {<class 'str'>}``. Card ids are slugs like ``"binary-search"``.

    The fakes in ``tests/test_rag_indexer.py`` happily recorded the call
    either way, so nothing failed until a real Milvus answered. Asserted on
    the *stored* rows rather than on the create call, because the point is
    that the write actually went through.
    """
    rows = indexed.query(
        collection_name=COLLECTION,
        filter=f'{ "card_id" } != ""',
        output_fields=["card_id"],
    )
    assert {row["card_id"] for row in rows} == {card.id for card in cards}


def test_reindexing_overwrites_rather_than_duplicating(
    indexed: Any, cards: list[Any]
) -> None:
    """Idempotence, against a real engine this time.

    With ``insert`` instead of ``upsert`` a second index run would leave two
    rows per card, and retrieval would spend part of its Top-N budget on a
    duplicate of something it already returned.
    """
    index_cards(indexed, COLLECTION, cards, _embed)

    rows = indexed.query(
        collection_name=COLLECTION,
        filter='card_id != ""',
        output_fields=["card_id"],
    )
    assert len(rows) == len(cards)


# ------------------------------------------------------------------ retrieval


def test_a_card_retrieves_itself(indexed: Any, cards: list[Any]) -> None:
    """Every card is findable by its own text.

    Not a semantic-quality claim — the embedder is lexical. It is the
    end-to-end claim: a card's text goes in through the indexer and comes
    back out of the search, in the right order, through the real client.
    """
    retriever = MilvusRetriever(indexed, COLLECTION, _embed)

    for card in cards:
        hits = retriever.search(card.embed_text, limit=3)
        assert any(hit.card_id == card.id for hit in hits), (
            f"{card.id} did not come back for its own text; got "
            f"{[h.card_id for h in hits]}"
        )


def test_the_hits_carry_the_fields_the_prompt_needs(
    indexed: Any, cards: list[Any]
) -> None:
    """``format_context`` renders title above text; a hit missing either
    would put a bare heading in the prompt. Milvus returns nothing for a
    field the indexer never wrote and does not say so, so this is checked
    on real output rather than trusted.
    """
    retriever = MilvusRetriever(indexed, COLLECTION, _embed)
    hits = retriever.search(cards[0].embed_text, limit=1)

    assert hits
    assert hits[0].title
    assert hits[0].text
    assert hits[0].score is not None


def test_a_tag_filter_narrows_the_hits(indexed: Any, cards: list[Any]) -> None:
    """Tag filtering happens after the search — see ``MilvusRetriever``'s
    docstring for why it is not pushed into a Milvus filter expression.
    Verified here that it still narrows, against real hits.
    """
    retriever = MilvusRetriever(indexed, COLLECTION, _embed)
    tags = list(cards[0].knowledge_tags)

    hits = retriever.search(cards[0].embed_text, tags=tags, limit=5)
    assert hits
    assert all(set(hit.tags) & set(tags) for hit in hits)


# ------------------------------------------------------------- into the graph


def test_the_graph_node_retrieves_real_context(
    indexed: Any, cards: list[Any]
) -> None:
    """S5 feeding S4. The node is not a placeholder any more — it hands the
    model something the corpus actually said.

    Runs the graph with the analysis stubbed out: what is under test is the
    ``retrieve`` node, and a real model call here would be a paid network
    request inside a test.
    """
    from unittest.mock import patch

    from app.agent import analyzer

    retriever = MilvusRetriever(indexed, COLLECTION, _embed)
    graph = build_analysis_graph(retriever, max_retries=0)

    with patch.object(analyzer, "chat_completion") as mock:
        mock.return_value = "{}"
        state = graph.invoke(
            {"code": "int main(){}", "statement": cards[0].embed_text},
            config={"configurable": {"thread_id": "rag-live"}},
        )

    assert state["retrieved"]
    assert state["context"]
    assert "###" in state["context"]
