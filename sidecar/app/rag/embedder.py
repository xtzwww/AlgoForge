"""Build the embedder that turns text into vectors (阶段 8 · `AGENTS.md` S5).

Why this module exists
----------------------
The retriever and the indexer both *take* an embedder; until 2026-09-21
nothing in the codebase ever *built* one. Every call site passed
``embedder=None``, so :func:`app.rag.retriever.build_retriever` degraded to
:class:`~app.rag.retriever.NullRetriever` and the retrieve node found
nothing. The knowledge base was implemented, tested against a real Milvus
engine — and unreachable from a real submission at the same time.

This module is the missing half: it turns configuration into a callable.

Two providers, one shape
------------------------
``Embedder`` is just ``Callable[[str], Sequence[float]]``
(:mod:`app.rag.schema`), which is why this module needs no LangChain
import. Both supported providers answer the same question:

* **ollama** — ``POST {base}/api/embeddings`` with ``{"model", "prompt"}``.
  Runs locally and costs nothing, which is what makes retrieval usable on
  a machine with no API budget.
* **openai_compatible** — ``POST {base}/embeddings`` with
  ``{"model", "input"}``, for hosted embedding endpoints.

Not every endpoint can do this. A chat-only endpoint — which is what most
free tiers are — has no embedding route at all: ask it for vectors and it
answers "model not found". That is a fact about the account, not a bug in
this code, so it is logged and answered with "no retriever" rather than an
exception.

The proxy trap
--------------
``httpx`` honours ``HTTP_PROXY`` / ``HTTPS_PROXY``. This machine sets them,
and a proxied request to ``127.0.0.1`` fails with a handshake error that
looks exactly like "the service is not running" — the same failure that
made Ollama and Milvus look broken during setup. Hence
``trust_env=False``: an embedder talks to one configured host and must not
be routed through whatever proxy the surrounding shell happens to export.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.config import settings
from app.rag.schema import Embedder

logger = logging.getLogger(__name__)

#: Provider selecting a local Ollama daemon.
OLLAMA = "ollama"

#: Provider selecting any OpenAI-compatible ``/embeddings`` endpoint.
OPENAI_COMPATIBLE = "openai_compatible"

#: Spellings that mean "do not embed anything". Deliberately generous: a
#: blank value is how someone says "not configured" in a hand-edited
#: ``.env``, and answering that with a warning is noise.
DISABLED = ("", "none", "off", "false", "disabled")

_client: Any | None = None
_client_key: tuple[str, str] | None = None


def reset_client_cache() -> None:
    """Forget the cached HTTP client.

    For tests, and for an operator who changed the embedding settings and
    restarted in-process: without this, a client built against the old
    base URL would keep being handed out.
    """
    global _client, _client_key
    _client = None
    _client_key = None


def _http_client(base_url: str, api_key: str, timeout: float) -> Any:
    """Return a pooled client for *base_url*, creating it at most once.

    One connection pool per ``(base_url, api_key)`` pair rather than one
    per embedding: building a client per call would add a TCP handshake
    (and, for the local daemon, a fresh connection) to every student
    submission, and the pool is safe to share because nothing here keeps
    per-request state.
    """
    global _client, _client_key
    key = (base_url, api_key)
    if _client is not None and _client_key == key:
        return _client

    import httpx  # noqa: PLC0415 — optional at import time, core at call time

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    _client = httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
        # See the module docstring: a proxied localhost request fails.
        trust_env=False,
    )
    _client_key = key
    return _client


def _make_ollama_embedder(
    base_url: str,
    model: str,
    timeout: float,
    keep_alive: str,
) -> Embedder:
    def embed(text: str) -> Sequence[float]:
        client = _http_client(base_url, "", timeout)
        # ``keep_alive`` keeps the model resident across calls. Without it
        # Ollama unloads after five minutes and the next submission pays
        # the full load again — measured at 52s for bge-m3 on this machine,
        # against ~4s for a call once it is warm. A student watching a
        # spinner cannot tell "loading the model" from "thinking".
        response = client.post(
            "/api/embeddings",
            json={"model": model, "prompt": text, "keep_alive": keep_alive},
        )
        response.raise_for_status()
        vector = response.json().get("embedding")
        if not vector:
            # An empty vector would reach Milvus and fail there with a
            # dimension error naming a number nobody recognises.
            raise ValueError(f"ollama returned no embedding for model {model!r}")
        return vector

    return embed


def _make_openai_embedder(
    base_url: str,
    model: str,
    timeout: float,
    api_key: str,
) -> Embedder:
    def embed(text: str) -> Sequence[float]:
        client = _http_client(base_url, api_key, timeout)
        response = client.post("/embeddings", json={"model": model, "input": text})
        response.raise_for_status()
        rows = response.json().get("data") or []
        if not rows:
            raise ValueError(f"embedding endpoint returned no data for {model!r}")
        return rows[0]["embedding"]

    return embed


def build_embedder() -> Embedder | None:
    """Return the configured embedder, or ``None`` when there is none.

    ``None`` is a normal answer, not an error: the caller turns it into
    "run without retrieval", which is what a deployment with no embedding
    model has always done. Raising instead would turn a missing optional
    dependency into a failed submission, and this runs inside the judging
    path — where a missing analysis is an enrichment gap and never a
    failed verdict.

    Returns:
        A callable mapping text to a vector, or ``None``.
    """
    if not settings.rag_enabled:
        logger.info("RAG_ENABLED is off; running without knowledge retrieval")
        return None

    provider = (settings.embedding_provider or "").strip().lower()
    if provider in DISABLED:
        logger.info("no embedding provider configured; knowledge retrieval off")
        return None

    model = (settings.embedding_model or "").strip()
    if not model:
        logger.warning("EMBEDDING_MODEL is empty; knowledge retrieval disabled")
        return None

    base_url = (settings.embedding_base_url or "").strip().rstrip("/")
    if not base_url:
        logger.warning("EMBEDDING_BASE_URL is empty; knowledge retrieval disabled")
        return None

    timeout = settings.embedding_timeout

    if provider == OLLAMA:
        return _make_ollama_embedder(
            base_url, model, timeout, settings.embedding_keep_alive or "30m"
        )
    if provider == OPENAI_COMPATIBLE:
        return _make_openai_embedder(
            base_url, model, timeout, settings.embedding_api_key or ""
        )

    logger.warning(
        "unknown EMBEDDING_PROVIDER %r (known: %s, %s); knowledge retrieval off",
        provider,
        OLLAMA,
        OPENAI_COMPATIBLE,
    )
    return None


def resolve_milvus_uri() -> str:
    """Where the knowledge collection lives: an embedded file, or a server.

    ``milvus-lite`` is Milvus's embedded build: hand
    :class:`~pymilvus.MilvusClient` a ``.db`` path and it runs the real
    engine in-process, with no Docker. That is what makes retrieval
    usable on this machine, where Docker cannot run. A machine that *can*
    run Milvus leaves ``MILVUS_LITE_PATH`` empty and gets the server.
    """
    lite = (settings.milvus_lite_path or "").strip()
    if lite:
        return lite
    return (settings.milvus_uri or "").strip() or "http://localhost:19530"
