"""The knowledge-tag aggregation, executed against a real PostgreSQL.

Why this file exists
--------------------
``tests/test_dialect_portability.py`` pins the *compiled* SQL for both
dialects, which is what the bug was. This file goes one step further and
**executes** the query on the database ``AGENTS.md`` actually mandates,
because compiling the right SQL and running the right SQL are different
claims and only the second one is the product working.

It is the automated form of two items that were otherwise blocked on this
machine: ``docs/项目欠缺清单.md`` §5.2 ("migrations were only ever
verified on SQLite") and the first step of ``docs/升级方案.md`` 阶段 0
("verify ``func.json_each`` actually works on real PostgreSQL"). Neither
could be done locally — there is no PostgreSQL here and the Docker route
is unavailable (``wsl.exe`` is blocked by policy), so CI is the only place
this project can reach a real server.

**Skipped, loudly, when no server is configured.** The skip reason says
the thing has *not* been measured, rather than reading as a pass — the
same rule ``pytest.ini`` states for the ``eval_set`` marker. To run it by
hand:

    ALGOFORGE_PG_TEST_URL=postgresql+psycopg://u:p@host:5432/db \\
        pytest tests/test_postgres_aggregation.py -q
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.knowledge_stats import compute_knowledge_stats
from app.models.problem import Problem
from app.models.submission import Submission

#: Set by the CI job that provides a PostgreSQL service container.
PG_URL_ENV = "ALGOFORGE_PG_TEST_URL"


@pytest.fixture
def pg_session() -> Iterator[Session]:
    """A session on a real PostgreSQL, or a skip naming the variable.

    The tables are created if absent, so this works whether or not the
    migrations were run first — and the CI job runs them first, which
    means this usually exercises the *migrated* schema rather than one
    ``create_all`` invented.
    """
    url = os.environ.get(PG_URL_ENV)
    if not url:
        pytest.skip(
            f"{PG_URL_ENV} is not set, so the aggregation has NOT been run "
            f"against a real PostgreSQL in this run"
        )

    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            # The server is a shared service container that outlives this
            # process, so start from a known-empty slate instead of
            # assuming one. Children before parents for the foreign keys.
            session.execute(delete(Submission))
            session.execute(delete(Problem))
            session.commit()
            yield session
    finally:
        engine.dispose()


def test_the_aggregation_executes_on_postgresql(pg_session: Session) -> None:
    """``compute_knowledge_stats`` on real PostgreSQL, not SQLite.

    This is the assertion that would have failed before the dialect fix:
    PostgreSQL has no ``json_each``, so the query raised
    ``function json_each(json) does not exist`` and the profile page had
    nothing to draw. Every other test in the suite runs on SQLite and
    could not have noticed.
    """
    problem = Problem(title="两数之积", knowledge_tags=["数学", "溢出"])
    pg_session.add(problem)
    pg_session.flush()  # assign the id the submissions point at
    pg_session.add_all(
        [
            Submission(problem_id=problem.id, status="AC"),
            Submission(problem_id=problem.id, status="WA"),
        ]
    )
    pg_session.commit()

    stats = {stat.tag: stat for stat in compute_knowledge_stats(pg_session)}

    # Both tags come off the same two submissions, so they must agree.
    assert set(stats) == {"数学", "溢出"}
    for stat in stats.values():
        assert stat.total == 2
        assert stat.passed == 1
        assert stat.accuracy == 0.5


def test_tags_come_back_as_strings_on_postgresql(pg_session: Session) -> None:
    """The tag label is text, not a JSON fragment.

    ``json_array_elements`` (without ``_text``) would return each element
    as ``json``, which renders as ``"数学"`` — with the quotes — and would
    silently break every tag comparison and the radar chart's axis
    labels. ``_text`` is what avoids that, and this is what pins it.
    """
    problem = Problem(title="标签类型", knowledge_tags=["动态规划"])
    pg_session.add(problem)
    pg_session.flush()
    # The aggregation inner-joins submissions, so a problem nobody has
    # submitted against produces no rows at all.
    pg_session.add(Submission(problem_id=problem.id, status="AC"))
    pg_session.commit()

    stats = compute_knowledge_stats(pg_session)

    assert [stat.tag for stat in stats] == ["动态规划"]
