"""Tests for the Submission model (PU-13).

PU-13 gave ``submissions`` its write path. Before that the table existed
but nothing inserted into it, while ``MistakeBookEntry`` required a
``submission_id`` that had to already exist — so the mistake book could
not be reached by its normal route (gap 7 in ``docs/PHASE-UX.md``).

The behavior worth guarding here is the one that decides whether the
model can represent what judging produces:

- a free run (no problem selected) must be storable, which is why
  ``problem_id`` became nullable;
- an unmeasured run must report *no* measurement rather than zero, so
  ``exec_time`` / ``memory_used`` stay NULL instead of defaulting to 0 —
  a CE submission ran nothing, and "0.0 s, 0 MB" would assert something
  false about it;
- a free run must not disturb a problem's accuracy statistics, which is
  what the knowledge-tag join's inner join gives us for free but is
  worth pinning because it is load-bearing and invisible.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.knowledge_stats import compute_knowledge_stats
from app.models.problem import Problem
from app.models.submission import STATUSES, Submission


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with the schema created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed_problem(
    session: Session, *, problem_id: int = 1, tags: list[str] | None = None
) -> Problem:
    problem = Problem(
        id=problem_id,
        title="Seeded",
        knowledge_tags=tags if tags is not None else ["基础"],
        description="",
        samples=[],
    )
    session.add(problem)
    session.commit()
    return problem


# --- Defaults ----------------------------------------------------------


def test_defaults_fill_the_required_text_columns(session: Session) -> None:
    """A minimally-constructed row is still valid.

    ``code`` / ``stdout`` / ``stderr`` are NOT NULL, so the model has to
    supply empty strings rather than None — otherwise an insert that
    forgets them fails on a NOT NULL violation instead of storing a row
    that says "no output".
    """
    _seed_problem(session)
    sub = Submission(problem_id=1, status="AC")
    session.add(sub)
    session.commit()

    assert sub.id == 1
    assert sub.code == ""
    assert sub.stdout == ""
    assert sub.stderr == ""
    assert sub.language == "cpp"
    assert sub.created_at is not None


def test_measurements_default_to_none(session: Session) -> None:
    """Time and memory are unmeasured unless stated.

    Defaulting to 0 would claim the run took no time and used no memory,
    which is a different statement from "nobody measured it".
    """
    _seed_problem(session)
    sub = Submission(problem_id=1, status="CE")
    session.add(sub)
    session.commit()

    assert sub.exec_time is None
    assert sub.memory_used is None


# --- Free runs ---------------------------------------------------------


def test_free_run_without_a_problem_is_storable(session: Session) -> None:
    """A run with no problem selected records fine.

    This is the reason ``problem_id`` became nullable: trying code out
    with nothing selected is legitimate, and dropping those rows would
    leave holes in the history the user cannot account for.
    """
    sub = Submission(problem_id=None, status="AC", code="int main(){}")
    session.add(sub)
    session.commit()

    fetched = session.get(Submission, sub.id)
    assert fetched is not None
    assert fetched.problem_id is None
    assert fetched.code == "int main(){}"


def test_unknown_problem_id_is_rejected(session: Session) -> None:
    """The foreign key still applies when a problem is given.

    Nullable is not the same as unconstrained: pointing at a problem that
    does not exist must still fail.

    SQLite does not enforce foreign keys unless ``PRAGMA
    foreign_keys=ON`` is set, and its default is OFF for backwards
    compatibility. PostgreSQL always enforces them, so without the pragma
    this test would pass on nothing — it would assert a constraint the
    engine had not even looked at.
    """
    from sqlalchemy import text

    session.execute(text("PRAGMA foreign_keys=ON"))
    session.add(Submission(problem_id=999, status="AC"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_free_run_does_not_affect_knowledge_stats(session: Session) -> None:
    """A run with no problem is excluded from accuracy statistics.

    The aggregation inner-joins submissions → problems, so a NULL
    ``problem_id`` drops out. That is the wanted outcome — a submission
    that is not about a problem must not move that problem's accuracy —
    but it is an emergent property of the join, so it is pinned here.
    """
    _seed_problem(session, problem_id=1, tags=["基础"])
    session.add(Submission(problem_id=1, status="AC"))
    session.add(Submission(problem_id=1, status="WA"))
    session.add(Submission(problem_id=None, status="WA", code="free"))
    session.commit()

    stats = compute_knowledge_stats(session)
    by_tag = {row.tag: row for row in stats}
    assert by_tag["基础"].total == 2, "the free run was counted"
    assert by_tag["基础"].passed == 1


# --- Full round trip ---------------------------------------------------


def test_full_round_trip_preserves_every_field(session: Session) -> None:
    """All PU-13 fields survive a commit, including CJK and newlines."""
    _seed_problem(session, problem_id=1, tags=["动态规划", "数组"])
    code = '#include <iostream>\n// 中文注释\nint main() { std::cout << "你好\\n"; }\n'
    sub = Submission(
        problem_id=1,
        status="WA",
        code=code,
        language="cpp",
        stdout="你好\n",
        stderr="runtime note: 未使用变量\n",
        exec_time=0.042,
        memory_used=17,
    )
    session.add(sub)
    session.commit()
    session.expunge_all()

    fetched = session.get(Submission, sub.id)
    assert fetched is not None
    assert fetched.code == code
    assert fetched.language == "cpp"
    assert fetched.stdout == "你好\n"
    assert fetched.stderr == "runtime note: 未使用变量\n"
    assert fetched.exec_time == pytest.approx(0.042)
    assert fetched.memory_used == 17
    assert fetched.status == "WA"


def test_large_source_is_not_truncated(session: Session) -> None:
    """``code`` is Text, not a bounded String.

    A truncated submission would be worse than a rejected one: the user
    would see their own code look wrong with no indication why.
    """
    _seed_problem(session)
    big = "// padding line\n" * 5000
    sub = Submission(problem_id=1, status="AC", code=big)
    session.add(sub)
    session.commit()
    session.expunge_all()

    fetched = session.get(Submission, sub.id)
    assert fetched is not None
    assert fetched.code == big
    assert len(fetched.code) == len(big)


@pytest.mark.parametrize("status", STATUSES)
def test_every_verdict_is_storable(status: str, session: Session) -> None:
    """All six verdicts fit the column.

    ``status`` is ``String(8)``; the longest verdict is three characters,
    so a future rename that lengthens one would fail here rather than in
    production.
    """
    _seed_problem(session)
    sub = Submission(problem_id=1, status=status)
    session.add(sub)
    session.commit()

    assert session.get(Submission, sub.id).status == status


# --- to_dict -----------------------------------------------------------


def test_to_dict_includes_source_by_default(session: Session) -> None:
    """The detail shape carries the code and the captured output."""
    _seed_problem(session)
    sub = Submission(
        problem_id=1,
        status="WA",
        code="int main(){}",
        stdout="3\n",
        stderr="boom\n",
        exec_time=1.5,
        memory_used=42,
    )
    session.add(sub)
    session.commit()

    d = sub.to_dict()
    assert d["code"] == "int main(){}"
    assert d["stdout"] == "3\n"
    assert d["stderr"] == "boom\n"
    assert d["exec_time"] == 1.5
    assert d["memory_used"] == 42
    assert d["problem_id"] == 1
    assert d["language"] == "cpp"


def test_to_dict_can_omit_the_source(session: Session) -> None:
    """The list shape drops the bulky text fields.

    A list view has no room for full source, and including it would make
    every page drag every submission's code across the wire.
    """
    _seed_problem(session)
    sub = Submission(problem_id=1, status="WA", code="int main(){}", stdout="3\n")
    session.add(sub)
    session.commit()

    d = sub.to_dict(include_source=False)
    assert "code" not in d
    assert "stdout" not in d
    assert "stderr" not in d
    # The summary fields stay — they are what the list renders.
    assert d["id"] == sub.id
    assert d["status"] == "WA"
    assert d["language"] == "cpp"


def test_to_dict_serializes_created_at_as_iso(session: Session) -> None:
    """``created_at`` goes out as a string, not a datetime object."""
    _seed_problem(session)
    sub = Submission(problem_id=1, status="AC")
    session.add(sub)
    session.commit()

    d = sub.to_dict()
    assert isinstance(d["created_at"], str)
    assert "T" in d["created_at"]


def test_to_dict_reports_unmeasured_values_as_null(session: Session) -> None:
    """Unmeasured stays null on the wire, matching the column."""
    _seed_problem(session)
    sub = Submission(problem_id=1, status="CE")
    session.add(sub)
    session.commit()

    d = sub.to_dict()
    assert d["exec_time"] is None
    assert d["memory_used"] is None


def test_to_dict_for_a_free_run_has_a_null_problem_id(session: Session) -> None:
    """A free run serializes with ``problem_id: null``."""
    sub = Submission(problem_id=None, status="AC", code="x")
    session.add(sub)
    session.commit()

    assert sub.to_dict()["problem_id"] is None


# --- Queries the API layer relies on ----------------------------------


def test_submissions_are_queryable_by_problem(session: Session) -> None:
    """Filtering by problem is what the list endpoint does."""
    _seed_problem(session, problem_id=1)
    _seed_problem(session, problem_id=2, tags=["图论"])
    session.add(Submission(problem_id=1, status="AC"))
    session.add(Submission(problem_id=1, status="WA"))
    session.add(Submission(problem_id=2, status="AC"))
    session.commit()

    rows = list(
        session.execute(
            select(Submission).where(Submission.problem_id == 1)
        ).scalars()
    )
    assert len(rows) == 2
    assert {r.status for r in rows} == {"AC", "WA"}


def test_problem_id_is_indexed(session: Session) -> None:
    """The column carries an index.

    Every list query filters on it, and SQLite reports indexes through
    ``PRAGMA index_list``. Asserting it here catches a model edit that
    drops ``index=True`` before it becomes a slow query in production.
    """
    from sqlalchemy import text

    names = {
        row[1]
        for row in session.execute(text("PRAGMA index_list('submissions')"))
    }
    assert "ix_submissions_problem_id" in names
