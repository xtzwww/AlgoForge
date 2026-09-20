"""Tests for the S8 memory seam (:mod:`app.agent.memory`).

The module's whole claim to exist is a fallback rule and one key format, so
that is what these tests pin:

* a configured-but-unimportable PostgreSQL backend degrades to memory
  instead of failing the analysis, and says why in the log;
* the checkpoint key is per *run*, because the shape of that key is the
  difference between "the retry inherits the previous answer" and "the
  retry starts clean" — see the module docstring for the measurement.

The carry-over behaviour itself is asserted in
``tests/test_agent_graph.py``, where the graph is the thing under test.
Here the key is only checked for what it contains.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest

from app.agent import memory
from app.config import settings


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    """Give each test its own shared backends.

    ``shared_checkpointer`` caches process-wide on purpose (that is what
    makes a run's history reachable afterwards), so without this one
    test's backend would leak into every other test in the session.
    """
    memory.reset_for_tests()
    yield
    memory.reset_for_tests()


# ------------------------------------------------------------------ the key


def test_the_key_names_the_submission_and_the_run() -> None:
    """Both parts, in that order — ``get_state_history`` has to be able to
    find a submission's runs without scanning every thread."""
    assert memory.thread_id_for(7, "abc123") == "submission:7:abc123"


def test_an_unrecorded_run_gets_its_own_namespace() -> None:
    """``None`` must not collapse into a shared bucket: two ad-hoc runs
    sharing a thread would have one inherit the other's analysis."""
    assert memory.thread_id_for(None, "abc123") == "submission:none:abc123"


def test_run_tokens_are_unique() -> None:
    """The one property the token has to have. A counter would restart
    with the process, which is fine for memory but not for PostgreSQL."""
    tokens = {memory.new_run_token() for _ in range(50)}
    assert len(tokens) == 50


# ------------------------------------------------------------- the fallback


def test_with_no_uri_the_checkpointer_is_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AGENTS.md`` §一: InMemorySaver is the development default."""
    monkeypatch.setattr(settings, "langgraph_checkpoint_uri", "")
    from langgraph.checkpoint.memory import InMemorySaver

    assert isinstance(memory.build_checkpointer(), InMemorySaver)


def test_a_uri_without_the_package_degrades_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The interesting case, and the one that is true on this machine:
    ``LANGGRAPH_CHECKPOINT_URI`` is set in ``.env`` but
    ``langgraph-checkpoint-postgres`` is not installed.

    Degrading is not enough on its own — "why did my checkpoint not
    survive a restart" has to be answerable from the log, so the log line
    is part of the contract.
    """
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(
        memory, "_postgres_available", lambda: (False, "not importable (test)")
    )

    with caplog.at_level(logging.INFO, logger="app.agent.memory"):
        from langgraph.checkpoint.memory import InMemorySaver

        assert isinstance(memory.build_checkpointer(), InMemorySaver)

    assert any("not importable (test)" in r.getMessage() for r in caplog.records)


def test_a_backend_that_fails_to_connect_degrades_too(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A configured, importable backend that cannot reach the database is
    the same trade: losing checkpointing must not lose the diagnosis.

    The backend is faked into ``sys.modules`` rather than monkeypatched on
    the real one, because the real one is not installed here — and this
    test is precisely about the branch that runs when it *is*.
    """
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", _FakePostgres())

    with caplog.at_level(logging.WARNING, logger="app.agent.memory"):
        from langgraph.checkpoint.memory import InMemorySaver

        assert isinstance(memory.build_checkpointer(), InMemorySaver)

    assert any("connection refused" in r.getMessage() for r in caplog.records)


def _FakePostgres() -> Any:
    """A stand-in ``langgraph.checkpoint.postgres`` whose connect raises."""

    module = types.ModuleType("langgraph.checkpoint.postgres")

    class PostgresSaver:
        @staticmethod
        def from_conn_string(uri: str) -> Any:
            raise OSError("connection refused")

    module.PostgresSaver = PostgresSaver
    return module


def test_the_store_follows_the_same_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long-term store, same fallback — asserted once rather than per
    backend, since it is the same three lines over a different import."""
    monkeypatch.setattr(settings, "langgraph_checkpoint_uri", "")
    from langgraph.store.memory import InMemoryStore

    assert isinstance(memory.build_store(), InMemoryStore)


# ------------------------------------------------------------------- sharing


def test_the_shared_backends_are_built_once() -> None:
    """Built per process, not per call: a checkpointer created and
    discarded inside one run keeps nothing anyone can read afterwards.
    """
    assert memory.shared_checkpointer() is memory.shared_checkpointer()
    assert memory.shared_store() is memory.shared_store()


def test_reset_for_tests_drops_the_cache() -> None:
    """Otherwise a test that builds a real backend inherits whichever one
    ran first."""
    first = memory.shared_checkpointer()
    memory.reset_for_tests()
    assert memory.shared_checkpointer() is not first
