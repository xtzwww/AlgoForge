"""Tests for app.models.knowledge_stats (P1-17).

Verifies the per-tag accuracy aggregation against an in-memory SQLite
database:

- Mixed AC/WA submissions on a problem with two tags → both tags
  share the same per-submission accuracy.
- A tag that appears on multiple problems aggregates across both.
- ``compute_knowledge_stats_for`` returns zero-count placeholders for
  requested tags with no submissions (radar-chart stability).
- Empty database → empty list (no rows to aggregate).

This file runs on SQLite, which is what the harness uses. The product's
database is PostgreSQL, and the tag expansion is dialect-specific, so the
same aggregation is also executed against a real server in
``tests/test_postgres_aggregation.py`` — a distinction that turned out to
matter (see ``app/models/tag_values.py``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.knowledge_stats import (
    compute_knowledge_stats,
    compute_knowledge_stats_for,
)
from app.models.problem import Problem
from app.models.submission import Submission


@pytest.fixture
def session() -> Session:
    """A fresh in-memory SQLite session with schema created.

    All models are created via ``Base.metadata.create_all`` so the test
    is independent of Alembic (which P1-17 doesn't wire up yet).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        # SQLite is this harness's database, not the product's. The tag
        # expansion being exercised is dialect-specific — see
        # app/models/tag_values.py for why, and
        # tests/test_postgres_aggregation.py for the run that happens on
        # the database the product actually uses.
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_problem(session: Session, title: str, tags: list[str]) -> Problem:
    p = Problem(title=title, knowledge_tags=tags)
    session.add(p)
    session.flush()  # assign id
    return p


def _add_submission(
    session: Session, problem_id: int, status: str
) -> Submission:
    s = Submission(problem_id=problem_id, status=status)
    session.add(s)
    return s


# --- Tests -------------------------------------------------------------

def test_empty_database_returns_empty_list(session: Session) -> None:
    """No submissions → no aggregated tags."""
    assert compute_knowledge_stats(session) == []


def test_single_problem_two_tags_shares_accuracy(session: Session) -> None:
    """A problem tagged [dp, 贪心] with 1 AC + 1 WA → both tags 0.5."""
    p = _add_problem(session, "背包问题", ["dp", "贪心"])
    _add_submission(session, p.id, "AC")
    _add_submission(session, p.id, "WA")
    session.commit()

    stats = compute_knowledge_stats(session)

    assert len(stats) == 2
    # Sorted by tag name ascending: "dp" < "贪心"
    by_tag = {s.tag: s for s in stats}
    assert set(by_tag) == {"dp", "贪心"}

    for stat in stats:
        assert stat.total == 2
        assert stat.passed == 1
        assert stat.accuracy == 0.5


def test_tag_aggregates_across_multiple_problems(session: Session) -> None:
    """The same tag on two problems sums across both."""
    p1 = _add_problem(session, "二分1", ["二分"])
    p2 = _add_problem(session, "二分2", ["二分"])
    # p1: 2 AC, 1 WA → 2/3
    _add_submission(session, p1.id, "AC")
    _add_submission(session, p1.id, "AC")
    _add_submission(session, p1.id, "WA")
    # p2: 1 AC, 1 TLE → 1/2
    _add_submission(session, p2.id, "AC")
    _add_submission(session, p2.id, "TLE")
    session.commit()

    stats = compute_knowledge_stats(session)

    # Only one tag overall.
    assert len(stats) == 1
    stat = stats[0]
    assert stat.tag == "二分"
    # Total: 3 + 2 = 5, passed: 2 + 1 = 3
    assert stat.total == 5
    assert stat.passed == 3
    assert stat.accuracy == 0.6


def test_non_ac_statuses_all_count_as_incorrect(session: Session) -> None:
    """WA/TLE/MLE/CE/RE all count as not-passed; only AC is correct."""
    p = _add_problem(session, "all-status", ["tag"])
    for status in ("AC", "WA", "TLE", "MLE", "CE", "RE"):
        _add_submission(session, p.id, status)
    session.commit()

    stats = compute_knowledge_stats(session)
    assert len(stats) == 1
    stat = stats[0]
    assert stat.total == 6
    assert stat.passed == 1  # only the AC
    assert stat.accuracy == round(1 / 6, 4)


def test_results_are_sorted_by_tag_name(session: Session) -> None:
    """compute_knowledge_stats returns stats sorted by tag name."""
    p3 = _add_problem(session, "p3", ["zzz"])
    p1 = _add_problem(session, "p1", ["aaa"])
    p2 = _add_problem(session, "p2", ["mmm"])
    # Each problem needs at least one submission for its tag to appear
    # in the aggregation (tags with zero submissions are not returned).
    _add_submission(session, p3.id, "WA")
    _add_submission(session, p1.id, "WA")
    _add_submission(session, p2.id, "WA")
    session.commit()

    stats = compute_knowledge_stats(session)
    tags = [s.tag for s in stats]
    assert tags == ["aaa", "mmm", "zzz"]


def test_to_dict_round_trip(session: Session) -> None:
    """to_dict() produces a JSON-safe dict with all expected keys."""
    p = _add_problem(session, "p", ["t"])
    _add_submission(session, p.id, "AC")
    session.commit()

    stat = compute_knowledge_stats(session)[0]
    d = stat.to_dict()
    assert d == {"tag": "t", "total": 1, "passed": 1, "accuracy": 1.0}


def test_compute_for_returns_placeholders_for_missing_tags(
    session: Session,
) -> None:
    """compute_knowledge_stats_for returns zero-count placeholders."""
    p = _add_problem(session, "p", ["known"])
    _add_submission(session, p.id, "AC")
    session.commit()

    # Request a tag that has no submissions plus the known one.
    stats = compute_knowledge_stats_for(session, ["unknown", "known"])

    assert len(stats) == 2
    assert stats[0].tag == "unknown"
    assert stats[0].total == 0
    assert stats[0].passed == 0
    assert stats[0].accuracy == 0.0

    assert stats[1].tag == "known"
    assert stats[1].total == 1
    assert stats[1].passed == 1
    assert stats[1].accuracy == 1.0


def test_compute_for_empty_tags_returns_empty(session: Session) -> None:
    """compute_knowledge_stats_for with no tags returns an empty list."""
    assert compute_knowledge_stats_for(session, []) == []


def test_compute_for_preserves_input_order(session: Session) -> None:
    """Order of results matches the order of requested tags."""
    p = _add_problem(session, "p", ["b", "a", "c"])
    _add_submission(session, p.id, "AC")
    session.commit()

    stats = compute_knowledge_stats_for(session, ["c", "a", "b"])
    assert [s.tag for s in stats] == ["c", "a", "b"]
