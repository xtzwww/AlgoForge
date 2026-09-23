"""Tests for app.db URL resolution (P1-20 data-layer cleanup).

The behaviour under test is that the documented PostgreSQL default is
actually *honored* rather than silently downgraded to a SQLite file.
Before app/db.py existed, mistake_book.py replaced any non-SQLite URL
with ``sqlite:///./codelab.db``, so DATABASE_URL had no effect at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.db import _connect_args, _resolve_sync_url, build_engine
from app.models.base import Base


def test_sqlite_url_passes_through() -> None:
    """SQLite URLs are used verbatim (this is how tests run)."""
    assert _resolve_sync_url("sqlite:///./codelab.db") == "sqlite:///./codelab.db"


def test_async_postgres_rewritten_to_sync_driver() -> None:
    """The asyncpg default must map to the blocking psycopg driver.

    The route handlers are synchronous, but DATABASE_URL is documented as
    ``postgresql+asyncpg``. Mapping (rather than discarding) the URL is
    what makes the PostgreSQL default usable.
    """
    async_url = "postgresql+asyncpg://u:p@localhost:5432/codelab"
    assert _resolve_sync_url(async_url) == "postgresql+psycopg://u:p@localhost:5432/codelab"


def test_sync_postgres_url_unchanged() -> None:
    """A URL that already names a sync driver is left alone."""
    url = "postgresql+psycopg2://u:p@db:5432/codelab"
    assert _resolve_sync_url(url) == url


def test_psycopg_driver_url_unchanged() -> None:
    """postgresql+psycopg is already synchronous."""
    url = "postgresql+psycopg://u:p@db:5432/codelab"
    assert _resolve_sync_url(url) == url


def test_database_url_sync_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL_SYNC takes precedence over rewriting DATABASE_URL."""
    from app.config import Settings

    monkeypatch.setenv(
        "DATABASE_URL_SYNC", "postgresql+psycopg://sync:pw@replica:5432/codelab"
    )
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/codelab"
    )
    import app.db as db_module

    monkeypatch.setattr(db_module, "settings", Settings(_env_file="nonexistent.env"))
    assert (
        db_module._resolve_sync_url(db_module.settings.database_url)
        == "postgresql+psycopg://sync:pw@replica:5432/codelab"
    )


def test_sqlite_connect_args_disable_thread_check() -> None:
    """SQLite needs check_same_thread=False."""
    assert _connect_args("sqlite:///./x.db") == {"check_same_thread": False}


def test_postgres_connect_args_include_a_timeout() -> None:
    """A PostgreSQL that is not there must be reported, not waited out.

    Measured: with the database down a request hung for over ninety
    seconds before failing. A ninety-second hang reads as "the app is
    broken"; a five-second failure reads as "the database is down",
    which is the true cause and the thing a person can act on.
    """
    args = _connect_args("postgresql+psycopg://u:p@localhost:5432/codelab")
    assert args.get("connect_timeout") == 5


def test_build_engine_for_sqlite() -> None:
    """build_engine produces a working engine for an in-memory SQLite DB."""
    engine = build_engine("sqlite://")
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("select 1").scalar() == 1
    finally:
        engine.dispose()


def test_build_engine_maps_asyncpg_to_psycopg() -> None:
    """An asyncpg URL yields a psycopg engine rather than an error.

    Constructing the engine resolves the DBAPI (imports psycopg) but does
    not open a connection, so this needs the driver installed but not a
    live PostgreSQL server.
    """
    engine = build_engine("postgresql+asyncpg://u:p@localhost:5432/codelab")
    try:
        assert engine.dialect.driver == "psycopg"
        assert "sqlite" not in engine.dialect.name
    finally:
        engine.dispose()


def test_the_schema_is_created_once_per_process_not_per_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_all`` must not run on every ``get_session()`` call.

    ``get_session`` is a *per-request* dependency, and ``create_all``
    reflects the existing schema — it queries the catalogue for every
    table — before deciding there is nothing to do. Per request that is N
    round trips for N tables, every time: invisible against a local
    database, and growing with the schema once there is real traffic.

    Counted rather than timed, so the assertion is about the number of
    calls and not about how fast the machine is.
    """
    import app.db as db_module

    # Another test may already have built the process-wide engine.
    db_module.reset_engine()

    calls: list[int] = []
    real_create_all = Base.metadata.create_all

    def counting_create_all(*args: object, **kwargs: object):
        calls.append(1)
        return real_create_all(*args, **kwargs)

    monkeypatch.setattr(db_module.Base.metadata, "create_all", counting_create_all)
    # A throwaway in-memory engine, so this never touches the configured
    # database (which on this machine does not exist).
    monkeypatch.setattr(
        db_module, "build_engine", lambda url=None: create_engine("sqlite://")
    )

    try:
        for _ in range(5):
            db_module.get_session().close()
    finally:
        db_module.reset_engine()

    assert len(calls) == 1, f"create_all ran {len(calls)} times for 5 sessions"


def test_a_failed_initialisation_is_retried_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow or failed `create_all` must not poison every later request.

    This is the failure behind a whole session of unexplained 500s.
    `create_all` against a database that is not accepting connections
    takes over a minute to give up. The old code assigned `_engine`
    *before* running it, so while that first request hung, every other
    request saw `_engine` set, skipped initialisation, and died on the
    bare `assert` in `get_session()` — `AssertionError`, naming neither
    the database nor the cause, permanently, until the process restarted.

    So the assertion is about the **second** call: it must surface the
    real error and try again, not inherit a half-built state.
    """
    import app.db as db_module

    db_module.reset_engine()

    attempts: list[int] = []

    def exploding_create_all(*args: object, **kwargs: object):
        attempts.append(1)
        raise RuntimeError("database is not accepting connections")

    monkeypatch.setattr(db_module.Base.metadata, "create_all", exploding_create_all)
    # `build_engine` succeeds — that is the point: the half-built state
    # comes from what happens *after* the engine exists.
    monkeypatch.setattr(
        db_module, "build_engine", lambda url=None: create_engine("sqlite://")
    )

    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="not accepting connections"):
                db_module.get_session()
    finally:
        db_module.reset_engine()

    # Both calls reported the real cause...
    assert len(attempts) == 2
    # ...and nothing was left half-published for the next request.
    assert db_module._engine is None
    assert db_module._SessionFactory is None
