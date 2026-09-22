"""Retrieval over the algorithm knowledge base (阶段 8 · `AGENTS.md` S4/S5).

Three pieces, deliberately kept separate:

* :mod:`app.rag.documents` — reads ``corpus/knowledge_cards/`` into card
  objects. Pure filesystem work, no external service, always available.
* :mod:`app.rag.indexer` — embeds those cards and writes them into Milvus.
  Without it the collection is empty and retrieval finds nothing.
* :mod:`app.rag.retriever` — turns a question into relevant cards, and
  degrades to "no context" when Milvus is unreachable rather than taking the
  analysis path down with it.

The indexer and the retriever take their embedder and Milvus client as
**injected collaborators**, so both are testable without ``pymilvus`` and
without a running Milvus. :mod:`app.rag.embedder` is what supplies a real
one at runtime — until 2026-09-21 nothing in the codebase built an
embedder at all, so the retrieve node ran and found nothing.

Verification status: indexing and retrieval have been exercised against a
real Milvus build since 2026-09-20 (``milvus-lite``, embedded, no Docker).
A Milvus **server** has still never been reachable from this repository.
"""

from app.rag.documents import (
    DEFAULT_CARDS_DIR,
    KnowledgeCard,
    cards_for_tags,
    load_knowledge_cards,
    parse_card,
)
from app.rag.embedder import (
    build_embedder,
    is_local_endpoint,
    reset_client_cache,
    resolve_milvus_uri,
)
from app.rag.indexer import build_records, index_cards
from app.rag.reranker import (
    build_reranker,
    describe_reranker,
    rerank_candidate_limit,
    rerank_cards,
)
from app.rag.retriever import (
    DEFAULT_COLLECTION,
    DEFAULT_LIMIT,
    MilvusRetriever,
    NullRetriever,
    RetrievedCard,
    Retriever,
    build_retriever,
    format_context,
)
from app.rag.schema import (
    FIELD_ERRORS,
    FIELD_ID,
    FIELD_PROBLEMS,
    FIELD_TAGS,
    FIELD_TEXT,
    FIELD_TITLE,
    FIELD_VECTOR,
    OUTPUT_FIELDS,
)

__all__ = [
    "DEFAULT_CARDS_DIR",
    "DEFAULT_COLLECTION",
    "DEFAULT_LIMIT",
    "FIELD_ERRORS",
    "FIELD_ID",
    "FIELD_PROBLEMS",
    "FIELD_TAGS",
    "FIELD_TEXT",
    "FIELD_TITLE",
    "FIELD_VECTOR",
    "KnowledgeCard",
    "MilvusRetriever",
    "NullRetriever",
    "OUTPUT_FIELDS",
    "RetrievedCard",
    "Retriever",
    "build_embedder",
    "build_records",
    "build_reranker",
    "build_retriever",
    "cards_for_tags",
    "describe_reranker",
    "format_context",
    "index_cards",
    "is_local_endpoint",
    "load_knowledge_cards",
    "parse_card",
    "rerank_candidate_limit",
    "rerank_cards",
    "reset_client_cache",
    "resolve_milvus_uri",
]
