"""Short-term and long-term memory for the analysis graph (S8).

Two different things, and conflating them is the usual mistake:

* **Short-term** — a *checkpointer*. LangGraph saves the graph's state
  after every node, keyed by a ``thread_id``. That is what makes a run
  resumable and auditable: "what did the graph know when it decided to
  retry" is a question about one submission.
* **Long-term** — a *store*. A cross-thread key/value space, which is
  where something learned from one submission that should survive into the
  next belongs (a student's recurring blind spot, a card that never
  helps). Nothing writes there yet; this module provides the seam so the
  first real use does not have to invent the wiring.

Why in-memory by default
------------------------
``AGENTS.md`` §一 specifies PostgreSQL for production and InMemorySaver for
development, and this module follows that — with one honest adjustment:

**the PostgreSQL implementations are not installed.** They live in separate
distributions (``langgraph-checkpoint-postgres``, and the store's
equivalent), not in ``langgraph`` itself. So the choice is made by
import, not by configuration alone: if the URI is set *and* the package
imports, PostgreSQL is used; otherwise it falls back to memory and says so
in the log.

That fallback is deliberately **loud once and then silent**: a missing
package is not an error the analysis should fail on, but it is also not
something to hide — "why did my checkpoint not survive a restart" is a
question the log has to be able to answer.

Process lifetime
----------------
An in-memory checkpointer dies with the process. That is fine for the
default pipeline (``direct``) which never compiles a graph, and fine for
development; it is the reason this is not silently presented as
"persistence".

Why the thread is per *run*, not per submission
-----------------------------------------------
This one is measured, not assumed, and it is the reason
:func:`thread_id_for` takes a run token.

LangGraph does not reset a thread between ``invoke`` calls. It loads the
previous checkpoint and applies the new input **on top of it**, so a
second ``invoke`` on ``"submission:7"`` starts with the first run's
``analysis``, ``error`` and ``notes`` already in state — and because
``attempts``, ``tool_calls`` and ``notes`` are ``operator.add``
accumulators in :class:`~app.agent.graph.AnalysisState`, they keep
growing. Concretely, a graph whose node returns ``{"attempts": 1}``
reports ``attempts == 2`` on the second run with the same thread.

For :func:`app.records_analyse.reanalyse_entry` that is not a curiosity:
the retry would hand the model the *previous* run's criticism as guidance
about an answer it never saw, and would report a model-call count that
includes calls this run did not make.

So the key is ``submission:<id>:<run>``. The submission part is still
there so a run's checkpoints can be found again by submission; the run
part is what keeps one run from inheriting another's answer.

What that leaves is *history*, not resumption:
``graph.get_state_history(config)`` walks every node's state for one run.
This graph is a single ``invoke`` that always reaches ``END``, so there is
no conversation to resume — the checkpoint is an audit trail, and
:class:`~app.agent.graph.GraphOutcome` carries the ``thread_id`` that
finds it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

#: What :func:`build_checkpointer` and :func:`build_store` accept as "off".
#: A blank URI means "no external store configured" — which is not an error,
#: it is the development default that AGENTS.md specifies.
_BLANK = ""


def _postgres_available() -> tuple[bool, str]:
    """Can the PostgreSQL backends be imported, and why not if they cannot."""
    try:
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401
    except ImportError as exc:
        return False, f"langgraph-checkpoint-postgres not importable ({exc})"
    try:
        from langgraph.store.postgres import PostgresStore  # noqa: F401
    except ImportError as exc:
        return False, f"the PostgreSQL store is not importable ({exc})"
    return True, ""


def build_checkpointer() -> Any:
    """A checkpointer for short-term, per-thread state.

    PostgreSQL when it is configured *and* importable, in-memory otherwise.
    Never raises: a graph that cannot checkpoint is still a graph that can
    answer, and losing checkpointing must not lose the diagnosis.
    """
    uri = (settings.langgraph_checkpoint_uri or _BLANK).strip()

    if uri:
        ok, why = _postgres_available()
        if ok:
            from langgraph.checkpoint.postgres import PostgresSaver

            try:
                return PostgresSaver.from_conn_string(uri)
            except Exception as exc:  # noqa: BLE001 — degrade, do not raise
                logger.warning(
                    "PostgreSQL checkpointer unavailable (%s: %s); "
                    "falling back to in-memory",
                    type(exc).__name__,
                    exc,
                )
        else:
            logger.info(
                "LANGGRAPH_CHECKPOINT_URI is set but %s; using the in-memory "
                "checkpointer — state will not survive a restart",
                why,
            )

    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def build_store() -> Any:
    """A store for long-term, cross-thread state.

    Same fallback rule as :func:`build_checkpointer`. Nothing writes to it
    yet — see the module docstring on why the seam exists before its first
    user.
    """
    uri = (settings.langgraph_checkpoint_uri or _BLANK).strip()

    if uri and _postgres_available()[0]:
        try:
            from langgraph.store.postgres import PostgresStore

            return PostgresStore.from_conn_string(uri)
        except Exception as exc:  # noqa: BLE001 — degrade, do not raise
            logger.warning(
                "PostgreSQL store unavailable (%s: %s); falling back to "
                "in-memory",
                type(exc).__name__,
                exc,
            )

    from langgraph.store.memory import InMemoryStore

    return InMemoryStore()


def new_run_token() -> str:
    """A fresh, short token identifying one graph run.

    Random rather than a counter: a counter restarts with the process, and
    a restarted process sharing an in-memory checkpointer is exactly the
    case that does not happen — but one sharing a *PostgreSQL* one does,
    and reusing a thread id there would inherit a run from the previous
    process. Twelve hex characters is enough to make that unreachable and
    still short enough to read in a log line.
    """
    return uuid.uuid4().hex[:12]


def thread_id_for(submission_id: Optional[int], run_token: str) -> str:
    """The checkpoint key for one *run* of one submission's analysis.

    Args:
        submission_id: The submission being analysed, or ``None`` for an
            unrecorded run (an ad-hoc judge against inline cases).
        run_token: From :func:`new_run_token`. Required rather than
            defaulted, because the one thing this key must not be is
            stable — see the module docstring for what inherits what when
            it is.

    ``None`` gets a namespace of its own rather than being folded into a
    shared bucket: two unrelated ad-hoc runs sharing a thread would have
    one inherit the other's analysis.
    """
    base = "submission:none" if submission_id is None else f"submission:{submission_id}"
    return f"{base}:{run_token}"


#: Built once per process and shared. Not per call: a checkpointer created
#: and discarded inside one ``run_analysis_graph`` call keeps nothing
#: anyone can read afterwards — the compiled graph goes out of scope with
#: it, so the checkpoints it wrote are unreachable. Holding the saver is
#: what makes ``get_state_history`` a question with an answer.
#:
#: The cost of holding it is that checkpoints accumulate for the life of
#: the process (a few tens of KB per run; the PostgreSQL backend carries
#: the same rows across restarts). Nothing trims them yet, because nothing
#: reads them yet beyond the thread id the outcome publishes.
_checkpointer: Any = None
_store: Any = None


def shared_checkpointer() -> Any:
    """The process-wide short-term store, built on first use."""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = build_checkpointer()
    return _checkpointer


def shared_store() -> Any:
    """The process-wide long-term store, built on first use."""
    global _store
    if _store is None:
        _store = build_store()
    return _store


def reset_for_tests() -> None:
    """Drop the cached backends so a test sees a fresh one.

    Not part of the product surface: this exists because the cache is
    process-wide, and a test that builds a real (PostgreSQL) checkpointer
    would otherwise inherit one from whichever test ran first.
    """
    global _checkpointer, _store
    _checkpointer = None
    _store = None
