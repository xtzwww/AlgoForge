"""The two ``knowledge_tags`` expansions must work on both supported dialects.

Why this file exists
--------------------
``problems.knowledge_tags`` is a JSON array, and SQLite and PostgreSQL have
**no common spelling** for "one row per element": SQLite has ``json_each``,
PostgreSQL has ``json_array_elements_text``, and neither has the other's.
(PostgreSQL 16's ``JSON_TABLE`` is a different construct, not a drop-in
replacement — the comment that claimed it was a superset is what let the
mistake survive.)

Both call sites used ``func.json_each(...)`` unconditionally, so the
profile page's per-tag accuracy aggregation and the problem catalogue's
tag filter would each have raised ``function json_each(json) does not
exist`` the first time they ran against the database ``AGENTS.md``
mandates. Nothing caught it because every test in the suite runs on SQLite
(``conftest`` monkeypatches ``get_session``), which is also why this file
is about **compiled SQL rather than executed SQL**: there is no PostgreSQL
on this machine. What is pinned here is the
rendered statement — which is exactly what was wrong, and exactly what a
future edit could silently break again.

The structural fix is a CI job backed by a real server; see the note at the
end of ``app/models/tag_values.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.knowledge_stats import compute_knowledge_stats
from app.models.tag_values import knowledge_tag_values

#: A DSN that is never connected to. ``create_engine`` is lazy and
#: ``Session.get_bind()`` does not connect either, so this yields a
#: PostgreSQL *dialect* with no server — which is the point: the branch
#: under test has to be reachable on a machine without PostgreSQL.
PG_DSN = "postgresql+psycopg://algoforge:algoforge@localhost:5432/algoforge"


@pytest.fixture
def sqlite_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def postgres_session() -> Iterator[Session]:
    engine = create_engine(PG_DSN)
    with Session(engine) as session:
        yield session


def _render(stmt, session: Session) -> str:
    """Compile *stmt* for the session's dialect, without executing it."""
    return str(stmt.compile(dialect=session.get_bind().dialect))


# ------------------------------------------------- the helper, per dialect


def test_sqlite_expands_tags_with_json_each(sqlite_session):
    stmt = select(knowledge_tag_values(sqlite_session).c.value)

    assert "json_each" in _render(stmt, sqlite_session)


def test_postgresql_does_not_expand_tags_with_json_each(postgres_session):
    """The regression this whole file exists for.

    PostgreSQL has no ``json_each``. Asserting the absence is the point —
    a test that only checked for ``json_array_elements_text`` would pass
    on a statement that used both.
    """
    stmt = select(knowledge_tag_values(postgres_session).c.value)
    sql = _render(stmt, postgres_session)

    assert "json_each" not in sql
    assert "json_array_elements_text" in sql


def test_the_two_spellings_agree_on_the_column_name(postgres_session, sqlite_session):
    """Everything around the expansion is dialect-independent.

    Both functions return one column named ``value``, which is what lets
    the ``select``/``group_by``/comparison around them stay identical.
    """
    for session in (sqlite_session, postgres_session):
        assert knowledge_tag_values(session).c.value is not None


def test_an_unknown_dialect_is_refused_rather_than_guessed(sqlite_session):
    """Refused, not defaulted — guessing is how the bug got in.

    Falling back to one spelling would emit SQL the server rejects, and
    the error would name a missing *function* rather than the real
    problem.
    """

    class _FakeDialect:
        name = "mysql"

    class _FakeBind:
        dialect = _FakeDialect()

    with (
        patch.object(sqlite_session, "get_bind", return_value=_FakeBind()),
        pytest.raises(NotImplementedError, match="mysql"),
    ):
        knowledge_tag_values(sqlite_session)


# ------------------------------------------- the call sites, not just the helper


def test_the_aggregation_statement_avoids_json_each_on_postgres(
    postgres_session, monkeypatch
):
    """``compute_knowledge_stats`` is a caller, and must stay one.

    Compiled rather than executed: ``execute`` is replaced so the
    statement can be inspected without a server. This is the profile
    page's query — the one that would have failed.
    """
    captured = {}

    class _EmptyResult:
        def all(self):
            return []

    def _capture(stmt, *args, **kwargs):
        captured["stmt"] = stmt
        return _EmptyResult()

    monkeypatch.setattr(postgres_session, "execute", _capture)
    compute_knowledge_stats(postgres_session)

    sql = _render(captured["stmt"], postgres_session)

    assert "json_each" not in sql
    assert "json_array_elements_text" in sql


def test_no_module_spells_the_expansion_by_hand():
    """Only ``tag_values`` may build the expansion.

    The bug was two independent copies of the same wrong line, so a check
    that there is only one copy is worth more than either copy's own test:
    it is what stops a third call site from re-inlining the wrong
    spelling.

    The pattern is the **call** form (``func.json_each(``), not the bare
    name, so that prose explaining the bug — including the comment in
    ``knowledge_stats.py`` that quotes the error PostgreSQL would have
    raised — does not trip it.
    """
    app_dir = Path(__file__).resolve().parents[1] / "sidecar" / "app"
    assert app_dir.is_dir(), f"expected the app package at {app_dir}"

    offenders = sorted(
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if path.name != "tag_values.py"
        and "func.json_each(" in path.read_text(encoding="utf-8")
    )

    assert offenders == [], (
        "these modules build the knowledge_tags expansion themselves instead of "
        f"using app.models.tag_values: {offenders}"
    )
