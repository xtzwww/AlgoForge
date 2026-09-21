"""Embed the knowledge-card corpus and write it into Milvus.

Run this once after configuring an embedding model, and again whenever the
model changes — vectors from two different models are not comparable, so a
collection holding both returns nonsense that looks like bad relevance
rather than a configuration error. Hence ``--recreate``.

Usage::

    sidecar/.venv/Scripts/python.exe tools/index_knowledge_base.py
    sidecar/.venv/Scripts/python.exe tools/index_knowledge_base.py --recreate
    sidecar/.venv/Scripts/python.exe tools/index_knowledge_base.py --probe "二分查找"

It exits non-zero (rather than printing a warning and succeeding) when no
embedder can be built: a silent no-op here is what left the knowledge base
unreachable for so long, and a script that "passes" while writing nothing
is worse than one that fails loudly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SIDECAR = Path(__file__).resolve().parents[1] / "sidecar"
if str(_SIDECAR) not in sys.path:
    sys.path.insert(0, str(_SIDECAR))

from app.config import settings  # noqa: E402
from app.rag import (  # noqa: E402
    DEFAULT_COLLECTION,
    build_embedder,
    format_context,
    index_cards,
    load_knowledge_cards,
    resolve_milvus_uri,
)

PROBE_QUERIES = (
    "二分查找的边界条件怎么写",
    "动态规划的状态转移方程",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop the collection first. Required after changing the model.",
    )
    parser.add_argument(
        "--probe",
        metavar="TEXT",
        help="Search for TEXT after indexing instead of the sample queries.",
    )
    args = parser.parse_args(argv)

    uri = resolve_milvus_uri()
    print(f"embedding provider : {settings.embedding_provider}")
    print(f"embedding model    : {settings.embedding_model}")
    print(f"embedding endpoint : {settings.embedding_base_url}")
    print(f"milvus             : {uri}")
    print(f"collection         : {DEFAULT_COLLECTION}")

    if not settings.rag_enabled:
        print("\nRAG_ENABLED is off — refusing to index.", file=sys.stderr)
        return 2

    embedder = build_embedder()
    if embedder is None:
        print(
            "\nNo embedder available. Check EMBEDDING_PROVIDER / "
            "EMBEDDING_MODEL / EMBEDDING_BASE_URL",
            file=sys.stderr,
        )
        return 2

    # milvus-lite needs the directory to exist; a missing parent fails
    # with an error that names neither the path nor the cause.
    if not uri.startswith(("http://", "https://")):
        Path(uri).parent.mkdir(parents=True, exist_ok=True)

    cards = load_knowledge_cards()
    print(f"\ncards              : {len(cards)}")
    if not cards:
        print("No cards found — nothing to index.", file=sys.stderr)
        return 2

    try:
        from pymilvus import MilvusClient
    except ImportError:
        print("\npymilvus is not installed.", file=sys.stderr)
        return 2

    client = MilvusClient(uri=uri)
    written = index_cards(
        client,
        DEFAULT_COLLECTION,
        cards,
        embedder,
        recreate=args.recreate,
    )
    print(f"indexed            : {written}")

    from app.rag import build_retriever  # noqa: PLC0415 — after the path setup

    retriever = build_retriever(uri, embedder=embedder)
    queries = (args.probe,) if args.probe else PROBE_QUERIES
    print("\n--- retrieval check ---")
    for query in queries:
        hits = retriever.search(query)
        print(f"\nquery: {query}")
        if not hits:
            print("  (no hits — the collection is empty or the embedder is wrong)")
            continue
        for hit in hits:
            print(f"  - {hit.title} (score {hit.score:.3f})")
        preview = format_context(hits)[:200].replace("\n", " ")
        print(f"  context preview: {preview}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
