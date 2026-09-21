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
from typing import Any, Optional

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

    The driver is faked rather than pointed at a dead host on purpose: a
    real ``psycopg.connect`` to an unreachable address *blocks*, so the
    warning under test would arrive after a TCP timeout instead of at
    once, and this test would look like a hang rather than a failure.
    """
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    _, _ = _install_fake_postgres(
        monkeypatch,
        "langgraph.checkpoint.postgres",
        "PostgresSaver",
        fail_on_connect=OSError("connection refused"),
    )

    with caplog.at_level(logging.WARNING, logger="app.agent.memory"):
        from langgraph.checkpoint.memory import InMemorySaver

        assert isinstance(memory.build_checkpointer(), InMemorySaver)

    assert any("connection refused" in r.getMessage() for r in caplog.records)


def test_the_store_follows_the_same_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long-term store, same fallback — asserted once rather than per
    backend, since it is the same three lines over a different import."""
    monkeypatch.setattr(settings, "langgraph_checkpoint_uri", "")
    from langgraph.store.memory import InMemoryStore

    assert isinstance(memory.build_store(), InMemoryStore)


# ------------------------------------------------- what the backend actually is
#
# The tests below exist because the bug they pin *only* appeared once the
# package was installed. Two wrong versions passed everything here first:
#
#   1. ``return PostgresSaver.from_conn_string(uri)`` stored a
#      ``_GeneratorContextManager`` → ``AttributeError`` on first write.
#   2. Entering the context manager and keeping the saver got further, then
#      died with ``psycopg.OperationalError: the connection is closed`` —
#      because ``from_conn_string`` does ``with Connection.connect(...)``,
#      so the connection dies with the generator.
#
# So the module now opens the connection itself and passes it to the
# constructor, and these tests pin *that*: the backend gets a live
# connection this module owns, and it is set up.


class _FakePsycopg:
    """Stands in for ``psycopg`` so no real server is needed.

    Returns a connection object whose ``closed`` flag the test can drive.
    """

    def __init__(self, fail_on_connect: Optional[BaseException] = None) -> None:
        self.fail_on_connect = fail_on_connect
        self.opened: list[_FakeConnection] = []

    def Connection(self) -> Any:  # noqa: N802 — mirrors psycopg's name
        opener = self

        class _Factory:
            @staticmethod
            def connect(uri: str, **kwargs: Any) -> Any:
                if opener.fail_on_connect is not None:
                    raise opener.fail_on_connect
                conn = _FakeConnection(uri, kwargs)
                opener.opened.append(conn)
                return conn

        return _Factory


class _FakeConnection:
    def __init__(self, uri: str, kwargs: dict[str, Any]) -> None:
        self.uri = uri
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _install_fake_postgres(
    monkeypatch: pytest.MonkeyPatch,
    backend_path: str,
    backend_cls_name: str,
    *,
    fail_on_connect: Optional[BaseException] = None,
) -> tuple[Any, Any]:
    """Point ``psycopg`` and one backend module at fakes.

    Returns the fake ``psycopg`` and the module holding the backend class,
    which records the constructed instances on ``.instances``.
    """
    fake_psycopg = _FakePsycopg(fail_on_connect)
    psycopg_module = types.ModuleType("psycopg")
    psycopg_module.Connection = fake_psycopg.Connection()
    rows_module = types.ModuleType("psycopg.rows")
    rows_module.dict_row = object()
    psycopg_module.rows = rows_module
    monkeypatch.setitem(sys.modules, "psycopg", psycopg_module)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_module)

    module = types.ModuleType(backend_path.split(".")[-1])
    module.instances = []

    class Backend:
        def __init__(self, conn: Any) -> None:
            self.conn = conn
            self.setup_calls = 0
            module.instances.append(self)

        def setup(self) -> None:
            self.setup_calls += 1

        def get_tuple(self, config: Any) -> str:
            if self.conn.closed:
                raise RuntimeError("the connection is closed")
            return "backend"

    globals()[backend_cls_name] = Backend
    setattr(module, backend_cls_name, Backend)
    monkeypatch.setitem(sys.modules, backend_path, module)
    return fake_psycopg, module


def test_the_backend_gets_a_connection_the_module_owns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression guard for both wrong versions.

    It must be a real backend over a connection that is still open after
    ``build_checkpointer`` returns — not the context manager (``AttributeError``)
    and not a backend whose connection died with it
    (``OperationalError: the connection is closed``).
    """
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    _, module = _install_fake_postgres(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver"
    )

    result = memory.build_checkpointer()

    assert module.instances == [result], "the backend was not constructed here"
    # This is the assertion both wrong versions failed: it is only a usable
    # checkpointer if the connection outlived the call.
    assert result.get_tuple({}) == "backend"
    assert result.conn.closed is False


def test_the_postgres_backend_is_set_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """``setup()`` creates the tables; skipping it turns the first write into
    an ``UndefinedTable``. Easy to forget, and nothing else notices."""
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    _, module = _install_fake_postgres(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver"
    )

    memory.build_checkpointer()

    assert module.instances[0].setup_calls == 1


def test_the_connection_uses_the_options_the_backend_assumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_conn_string`` opens with autocommit, ``prepare_threshold=0``
    and ``dict_row``; the backends' SQL assumes all three. Dropping one
    fails at query time, not at connect time."""
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    fake_psycopg, _ = _install_fake_postgres(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver"
    )

    memory.build_checkpointer()

    kwargs = fake_psycopg.opened[0].kwargs
    assert kwargs.get("autocommit") is True
    assert kwargs.get("prepare_threshold") == 0
    assert "row_factory" in kwargs


def test_a_setup_that_fails_degrades_rather_than_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``setup()`` runs inside the same try as the connect, so a schema it
    cannot create is the same trade as an unreachable server: lose
    checkpointing, keep the diagnosis."""
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    fake_psycopg, module = _install_fake_postgres(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver"
    )

    def _explode() -> None:
        raise RuntimeError("permission denied for schema public")

    module.PostgresSaver.setup = lambda self: _explode()

    with caplog.at_level(logging.WARNING, logger="app.agent.memory"):
        from langgraph.checkpoint.memory import InMemorySaver

        assert isinstance(memory.build_checkpointer(), InMemorySaver)

    assert any("permission denied" in r.getMessage() for r in caplog.records)
    # The failed connection is closed rather than leaked.
    assert fake_psycopg.opened[0].closed is True


def test_the_store_is_wired_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store had the identical bug in its identical line."""
    monkeypatch.setattr(
        settings, "langgraph_checkpoint_uri", "postgresql://u:p@localhost/db"
    )
    monkeypatch.setattr(memory, "_postgres_available", lambda: (True, ""))
    _, module = _install_fake_postgres(
        monkeypatch, "langgraph.store.postgres", "PostgresStore"
    )

    result = memory.build_store()

    assert module.instances == [result]
    assert result.setup_calls == 1
    assert result.conn.closed is False


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
