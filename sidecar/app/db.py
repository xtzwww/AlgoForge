"""Database engine and session management.

This is the single place that knows how to turn ``settings.database_url``
into an engine. It previously lived inside ``app/api/mistake_book.py``
and was imported by ``profile.py`` and ``review.py``, which meant the
API layer owned the data layer and a route module had to be imported
just to get a DB session.

Resolution rules
----------------
``DATABASE_URL`` is the production PostgreSQL DSN (``postgresql+asyncpg``
per AGENTS.md). The route handlers are all synchronous, so:

* A **sync** driver URL (``sqlite://``, ``postgresql+psycopg2://``,
  ``postgresql+psycopg://``) is used verbatim.
* An **async** driver URL (``postgresql+asyncpg://``) is rewritten to the
  sync ``psycopg`` driver for these blocking routes. This is what lets
  the documented PostgreSQL default actually be used instead of silently
  falling back to a SQLite file.
* ``DATABASE_URL_SYNC`` overrides all of the above when set, so an
  operator can point the sync routes at a different DSN.

Tests get SQLite by monkeypatching ``get_session`` (see the test
modules), which keeps them fast, hermetic, and free of a live Postgres.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base

logger = logging.getLogger(__name__)

# Process-wide engine / session factory. Built lazily on first use so
# that importing this module never touches a database (tests can patch
# get_session() before anything is constructed) and so a misconfigured
# DSN only fails when a request actually needs the DB.
_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker[Session]] = None

# Driver prefixes that indicate an async SQLAlchemy driver. The routes in
# this project are synchronous, so these are mapped to their blocking
# equivalent before create_engine sees them.
_ASYNC_TO_SYNC_DRIVER = {
    "postgresql+asyncpg": "postgresql+psycopg",
    "postgresql+psycopg_async": "postgresql+psycopg",
}


def _resolve_sync_url(url: str) -> str:
    """Return a blocking-driver URL for *url*.

    An explicit ``DATABASE_URL_SYNC`` always wins. Otherwise async
    Postgres drivers are rewritten to their sync counterpart; every other
    URL (including ``sqlite://``) is returned unchanged.
    """
    override = getattr(settings, "database_url_sync", None)
    if override:
        return override
    for async_prefix, sync_prefix in _ASYNC_TO_SYNC_DRIVER.items():
        if url.startswith(async_prefix):
            rest = url[len(async_prefix):]
            return sync_prefix + rest
    return url


def _connect_args(url: str) -> dict:
    """Driver-specific connect args.

    SQLite needs ``check_same_thread=False`` because the session may be
    used from a different thread than the one that created the
    connection (the judge runs in a worker thread).
    """
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def build_engine(url: Optional[str] = None) -> Engine:
    """Build a (but do not cache) engine for *url* — defaults to settings."""
    raw = url if url is not None else (settings.database_url or "")
    sync_url = _resolve_sync_url(raw)
    logger.debug("creating DB engine for %s", sync_url.split("@")[-1])
    return create_engine(sync_url, connect_args=_connect_args(sync_url))


def _ensure_engine() -> None:
    """Build the process-wide engine + session factory on first use.

    The schema check lives here rather than in :func:`get_session`.
    ``get_session`` is a *per-request* dependency, and ``create_all``
    reflects the existing schema — it queries the catalogue for every
    table — before concluding there is nothing to do. Calling it per
    request therefore costs N catalogue round trips for N tables on every
    single request. A local SQLite file and a same-machine PostgreSQL hide
    that; it stops being hidden as soon as there is real traffic, and the
    cost grows with the schema, which is the worst shape a performance
    trap can have.

    Running it once per process keeps the property the per-request call
    existed for — a fresh development database is immediately usable —
    while removing the per-request cost.

    Caveat this does **not** fix: ``create_all`` builds whatever the ORM
    models currently declare, so against a database Alembic manages it can
    create a table the migrations have not reached yet, leaving the two
    out of step. Alembic remains the source of truth for schema changes
    (AGENTS.md §二); this is a development convenience, not a migration
    strategy.
    """
    global _engine, _SessionFactory
    if _engine is not None:
        return
    _engine = build_engine()
    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(_engine, expire_on_commit=False)


def get_session() -> Session:
    """Return a new session bound to the process-wide engine.

    Callers use this as a context manager (``with get_session() as s:``).
    """
    _ensure_engine()
    assert _SessionFactory is not None  # narrowed by _ensure_engine
    return _SessionFactory()


def reset_engine() -> None:
    """Drop the cached engine and session factory.

    Used by tests and by anyone who changes DATABASE_URL at runtime.
    """
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
