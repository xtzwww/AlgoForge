"""Retrieval over the algorithm knowledge base (阶段 8 · `AGENTS.md` S4/S5).

Two pieces, deliberately kept separate:

* :mod:`app.rag.documents` — reads ``corpus/knowledge_cards/`` into card
  objects. Pure filesystem work, no external service, always available.
* :mod:`app.rag.retriever` — turns a question into relevant cards, and
  degrades to "no context" when Milvus is unreachable rather than taking the
  analysis path down with it.

Not written yet: the indexer that embeds the corpus and writes it into Milvus.
Until it exists, :class:`~app.rag.retriever.MilvusRetriever` has nothing to
search — the corpus is on disk but not in any collection.
"""

from app.rag.documents import (
    DEFAULT_CARDS_DIR,
    KnowledgeCard,
    cards_for_tags,
    load_knowledge_cards,
    parse_card,
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

__all__ = [
    "DEFAULT_CARDS_DIR",
    "DEFAULT_COLLECTION",
    "DEFAULT_LIMIT",
    "KnowledgeCard",
    "MilvusRetriever",
    "NullRetriever",
    "RetrievedCard",
    "Retriever",
    "build_retriever",
    "cards_for_tags",
    "format_context",
    "load_knowledge_cards",
    "parse_card",
]
