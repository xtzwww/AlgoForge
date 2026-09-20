"""The vocabulary the indexer and the retriever must agree on.

This module exists because they *did* drift. The indexer declared field-name
constants with a comment saying they were there so the writer and the reader
"cannot drift apart" — while the retriever went on passing its own hardcoded
list of field names. A field the retriever asks for but the indexer never
writes does not raise: Milvus just returns nothing for it, so retrieval
degrades to empty context with no error to point at.

Putting the names (and the embedder signature they are exchanged under) in one
importable place makes the agreement structural instead of aspirational.

Nothing here talks to Milvus; it is pure vocabulary.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

#: Minimal shape of an embedder. Matches LangChain's ``Embeddings`` interface
#: (``AGENTS.md`` §一 locks embedding to it) without importing it — the same
#: reason ``pymilvus`` is not imported at module scope: a module that cannot be
#: imported without an optional dependency cannot be unit-tested either.
Embedder = Callable[[str], Sequence[float]]

#: Primary key. ``card_id`` rather than a generated id so re-indexing a card
#: overwrites it instead of adding a second row for the same knowledge point.
#:
#: A **string** key, which is why :func:`app.rag.indexer.index_cards` has to
#: pass ``id_type="string"`` when it creates the collection: Milvus defaults
#: the primary key to INT64, and every upsert then fails.
FIELD_ID = "card_id"

#: Declared length of that varchar. Ids are short slugs (``"binary-search"``),
#: but Milvus needs a number and an underestimate is a schema error on write
#: rather than something a test with fakes can catch.
MAX_ID_LENGTH = 256

#: The embedding itself.
FIELD_VECTOR = "vector"

FIELD_TITLE = "title"

#: The card body — *not* the embedded text, which also contains the title.
#: :func:`app.rag.retriever.format_context` renders the title above this.
FIELD_TEXT = "text"

#: Knowledge tags, used to filter results by the problem's own tags.
FIELD_TAGS = "knowledge_tags"

FIELD_PROBLEMS = "related_problems"
FIELD_ERRORS = "common_errors"

#: Fields the retriever asks Milvus to return. Must be a subset of what the
#: indexer writes — ``tests/test_rag_indexer.py`` asserts exactly that.
OUTPUT_FIELDS: tuple[str, ...] = (FIELD_ID, FIELD_TITLE, FIELD_TEXT, FIELD_TAGS)
