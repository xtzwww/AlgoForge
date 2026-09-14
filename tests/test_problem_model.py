"""Tests for the PU-11 problem-catalogue models.

Covers the two things PU-11 adds to the data layer:

1. ``Problem``'s new columns (statement, display samples, judge limits,
   catalogue metadata, soft-delete flag) — that they persist, that their
   defaults are the documented ones, and that ``to_dict`` shapes the
   payload the API layer will return.
2. The new ``TestCase`` table — that it stores hidden cases, that
   ``ordinal`` orders them, and that the (problem_id, ordinal) unique
   constraint actually rejects a duplicate slot.

Uses the same in-memory SQLite + ``StaticPool`` pattern as
``test_mistake_book.py``: a single shared connection so the schema and the
rows live in one database for the whole test.

The hidden/visible split is the load-bearing idea here, so it gets its own
test rather than being folded into a generic "columns persist" check: if
``is_sample`` filtering silently stopped working, every hidden case would
become public and the judge would stop discriminating between submissions.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.problem import (
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_TIME_LIMIT,
    DIFFICULTIES,
    Problem,
)
from app.models.test_case import TestCase

# No alias for TestCase: pytest.ini sets ``python_classes = Test*Suite``
# precisely because this model is named TestCase. See the note there.


@pytest.fixture
def session() -> Iterator[Session]:
    """Fresh in-memory SQLite session with the full schema created.

    ``app.models`` is imported rather than individual modules so every
    table registers on ``Base.metadata`` — that import is what makes this
    fixture immune to someone adding a model and forgetting to wire it in.
    """
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


def _make_problem(**overrides) -> Problem:
    """A minimal valid Problem; overrides win."""
    fields = {"title": "A + B", "knowledge_tags": ["基础语法"]}
    fields.update(overrides)
    return Problem(**fields)


# --- Problem: defaults --------------------------------------------------


def test_problem_defaults_match_the_documented_limits(session: Session) -> None:
    """A problem that states no limits gets the pre-PU-11 judge constants.

    Regression guard: the judge request used to hardcode 10.0s / 256 MB, so
    these defaults are what keeps old behaviour intact for problems that
    never set their own limits.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()
    session.refresh(problem)

    assert problem.description == ""
    assert problem.input_format is None
    assert problem.output_format is None
    assert problem.samples == []
    assert problem.time_limit == DEFAULT_TIME_LIMIT == 10.0
    assert problem.memory_limit == DEFAULT_MEMORY_LIMIT == 256
    assert problem.difficulty is None
    assert problem.source is None
    assert problem.is_active is True


def test_problem_stores_full_catalogue_fields(session: Session) -> None:
    """Every PU-11 column round-trips through the database."""
    problem = _make_problem(
        title="二分查找",
        knowledge_tags=["二分", "数组"],
        description="给定一个升序数组，查找目标值的下标。",
        input_format="第一行 n 和 target，第二行 n 个整数。",
        output_format="一行，下标或 -1。",
        samples=[
            {"input": "5 3\n1 2 3 4 5", "output": "2"},
            {"input": "3 9\n1 2 3", "output": "-1"},
        ],
        time_limit=2.5,
        memory_limit=128,
        difficulty="easy",
        source="POJ 1000",
    )
    session.add(problem)
    session.commit()
    # Drop the identity map so the assertions read real column values
    # rather than the Python objects we just handed to SQLAlchemy.
    session.expunge_all()

    loaded = session.execute(select(Problem)).scalar_one()
    assert loaded.title == "二分查找"
    assert loaded.knowledge_tags == ["二分", "数组"]
    assert loaded.description == "给定一个升序数组，查找目标值的下标。"
    assert loaded.input_format == "第一行 n 和 target，第二行 n 个整数。"
    assert loaded.output_format == "一行，下标或 -1。"
    assert loaded.samples == [
        {"input": "5 3\n1 2 3 4 5", "output": "2"},
        {"input": "3 9\n1 2 3", "output": "-1"},
    ]
    assert loaded.time_limit == 2.5
    assert loaded.memory_limit == 128
    assert loaded.difficulty == "easy"
    assert loaded.source == "POJ 1000"


def test_problem_difficulty_is_nullable_and_chinese_tags_survive(
    session: Session,
) -> None:
    """An unclassified problem stays NULL rather than defaulting to medium.

    Also covers the encoding path: this project's tags are Chinese, and a
    JSON column that mangles them would poison the knowledge-statistics
    aggregation that P1-17 already builds on.
    """
    session.add(_make_problem(title="未分类题", knowledge_tags=["动态规划", "区间DP"]))
    session.commit()
    session.expunge_all()

    loaded = session.execute(select(Problem)).scalar_one()
    assert loaded.difficulty is None
    assert loaded.knowledge_tags == ["动态规划", "区间DP"]


def test_difficulties_constant_is_the_agreed_vocabulary() -> None:
    """The module constant is what the loader/API/tests agree on."""
    assert DIFFICULTIES == ("easy", "medium", "hard")


# --- Problem: soft delete ------------------------------------------------


def test_inactive_problem_can_be_filtered_out(session: Session) -> None:
    """``is_active`` supports the catalogue's soft-delete query.

    Retiring a problem must not delete the row — ``submissions`` and
    ``mistake_book`` hold foreign keys to it, so a hard delete would orphan
    a user's history.
    """
    session.add_all(
        [
            _make_problem(title="在售", is_active=True),
            _make_problem(title="下架", is_active=False),
        ]
    )
    session.commit()

    active = (
        session.execute(select(Problem).where(Problem.is_active.is_(True)))
        .scalars()
        .all()
    )
    assert [p.title for p in active] == ["在售"]
    # The row still exists — this is a flag, not a delete.
    assert session.execute(select(Problem)).scalars().all().__len__() == 2


# --- Problem: serialization ---------------------------------------------


def test_problem_to_dict_includes_statement_by_default(session: Session) -> None:
    """The detail payload carries the prose and the display samples."""
    problem = _make_problem(
        description="正文",
        input_format="输入格式",
        samples=[{"input": "1 2", "output": "3"}],
        difficulty="easy",
    )
    session.add(problem)
    session.commit()

    data = problem.to_dict()
    assert data["title"] == "A + B"
    assert data["description"] == "正文"
    assert data["input_format"] == "输入格式"
    assert data["output_format"] is None
    assert data["samples"] == [{"input": "1 2", "output": "3"}]
    assert data["difficulty"] == "easy"
    assert data["is_active"] is True
    # The fields the catalogue list needs are present too.
    assert data["knowledge_tags"] == ["基础语法"]
    assert data["time_limit"] == DEFAULT_TIME_LIMIT


def test_problem_to_dict_can_omit_the_statement(session: Session) -> None:
    """The list endpoint can skip the long prose fields.

    A catalogue page that ships every statement just to render a list of
    titles is waste, so ``include_statement=False`` drops them — and drops
    them *entirely*, rather than sending empty strings that a caller might
    mistake for "this problem has no statement".
    """
    session.add(_make_problem(description="正文", input_format="输入格式"))
    session.commit()
    problem = session.execute(select(Problem)).scalar_one()

    data = problem.to_dict(include_statement=False)
    assert "description" not in data
    assert "input_format" not in data
    assert "output_format" not in data
    assert data["knowledge_tags"] == ["基础语法"]


# --- TestCase -----------------------------------------------------------


def test_test_case_round_trips_and_orders_by_ordinal(session: Session) -> None:
    """Cases come back in authored order, not insertion or id order.

    The user is told "case 2 of 5", so the numbering has to be stable and
    author-controlled. Inserting out of order is exactly the case that
    would be wrong if ordering relied on ``id``.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    session.add_all(
        [
            TestCase(
                problem_id=problem.id,
                ordinal=2,
                input="2 2",
                expected_output="4",
                is_sample=False,
            ),
            TestCase(
                problem_id=problem.id,
                ordinal=1,
                input="1 1",
                expected_output="2",
                is_sample=True,
            ),
            TestCase(
                problem_id=problem.id,
                ordinal=3,
                input="3 3",
                expected_output="6",
                is_sample=False,
            ),
        ]
    )
    session.commit()
    session.expunge_all()

    ordered = (
        session.execute(
            select(TestCase)
            .where(TestCase.problem_id == problem.id)
            .order_by(TestCase.ordinal)
        )
        .scalars()
        .all()
    )
    assert [c.ordinal for c in ordered] == [1, 2, 3]
    assert [c.expected_output for c in ordered] == ["2", "4", "6"]


def test_hidden_cases_are_distinguishable_from_samples(session: Session) -> None:
    """``is_sample`` is what keeps judging cases off a public response.

    Before PU-11 all cases travelled to the client in the judge request,
    which meant a user could read the expected output and hardcode it. This
    asserts the data layer can tell the two apart — PU-12 is responsible
    for not serving the hidden ones.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    session.add_all(
        [
            TestCase(
                problem_id=problem.id,
                ordinal=1,
                input="3 5",
                expected_output="8",
                is_sample=True,
            ),
            TestCase(
                problem_id=problem.id,
                ordinal=2,
                input="1000000000 1000000000",
                expected_output="2000000000",
                is_sample=False,
            ),
        ]
    )
    session.commit()

    samples = (
        session.execute(
            select(TestCase).where(
                TestCase.problem_id == problem.id, TestCase.is_sample.is_(True)
            )
        )
        .scalars()
        .all()
    )
    hidden = (
        session.execute(
            select(TestCase).where(
                TestCase.problem_id == problem.id, TestCase.is_sample.is_(False)
            )
        )
        .scalars()
        .all()
    )

    assert [c.input for c in samples] == ["3 5"]
    assert [c.input for c in hidden] == ["1000000000 1000000000"]


def test_test_case_defaults_to_hidden(session: Session) -> None:
    """A case is hidden unless explicitly marked as a sample.

    Fail-closed: a loader that forgets to set ``is_sample`` leaks nothing,
    whereas the opposite default would leak every case it forgot about.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    session.add(
        TestCase(problem_id=problem.id, ordinal=1, input="1", expected_output="1")
    )
    session.commit()
    session.expunge_all()

    assert session.execute(select(TestCase)).scalar_one().is_sample is False


def test_duplicate_ordinal_within_a_problem_is_rejected(session: Session) -> None:
    """Two cases cannot claim the same slot, so numbering stays unambiguous.

    Without the constraint a re-import could duplicate positions and the
    reported case number would depend on row order.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    session.add(
        TestCase(problem_id=problem.id, ordinal=1, input="a", expected_output="a")
    )
    session.commit()

    session.add(
        TestCase(problem_id=problem.id, ordinal=1, input="b", expected_output="b")
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_same_ordinal_is_allowed_across_different_problems(session: Session) -> None:
    """The constraint is per-problem, not global.

    Every problem starts at ordinal 1; a global unique index would make the
    second problem impossible to author.
    """
    first = _make_problem(title="题一")
    second = _make_problem(title="题二")
    session.add_all([first, second])
    session.commit()

    session.add_all(
        [
            TestCase(problem_id=first.id, ordinal=1, input="1", expected_output="1"),
            TestCase(problem_id=second.id, ordinal=1, input="2", expected_output="2"),
        ]
    )
    session.commit()

    assert session.execute(select(TestCase)).scalars().all().__len__() == 2


def test_ordinal_may_start_at_a_different_number(session: Session) -> None:
    """The column stores whatever ordinal it is given; it is not computed.

    Deliberately loose: PU-14's loader decides whether a catalogue starts
    at 0 or 1. Pinning the semantics here would mean changing the model to
    change the loader's convention.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    session.add(
        TestCase(problem_id=problem.id, ordinal=0, input="x", expected_output="x")
    )
    session.commit()

    assert session.execute(select(TestCase)).scalar_one().ordinal == 0


def test_empty_input_is_a_valid_case(session: Session) -> None:
    """A program that reads nothing is judged against an empty stdin.

    Empty string is the default for both text columns rather than NULL, so
    callers never have to distinguish "no input" from "input not set".
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    session.add(
        TestCase(problem_id=problem.id, ordinal=1, input="", expected_output="hello")
    )
    session.commit()
    session.expunge_all()

    loaded = session.execute(select(TestCase)).scalar_one()
    assert loaded.input == ""
    assert loaded.expected_output == "hello"


def test_multiline_io_survives_the_round_trip(session: Session) -> None:
    """Real cases are multi-line; newlines and trailing spaces must persist.

    Comparison normalizes presentation, but storage must not — a case
    silently stripped on the way in would change what the judge accepts.
    """
    problem = _make_problem()
    session.add(problem)
    session.commit()

    stdin = "5\n1 2 3 4 5\n"
    stdout = "15\nsecond line  \n\n"
    session.add(
        TestCase(
            problem_id=problem.id,
            ordinal=1,
            input=stdin,
            expected_output=stdout,
        )
    )
    session.commit()
    session.expunge_all()

    loaded = session.execute(select(TestCase)).scalar_one()
    assert loaded.input == stdin
    assert loaded.expected_output == stdout


def test_test_case_to_dict_shapes_the_api_payload(session: Session) -> None:
    """``to_dict`` exposes exactly the judging-relevant fields."""
    problem = _make_problem()
    session.add(problem)
    session.commit()

    case = TestCase(
        problem_id=problem.id,
        ordinal=2,
        input="3 4",
        expected_output="7",
        is_sample=False,
    )
    session.add(case)
    session.commit()

    data = case.to_dict()
    assert data == {
        "id": case.id,
        "problem_id": problem.id,
        "ordinal": 2,
        "input": "3 4",
        "expected_output": "7",
        "is_sample": False,
    }


def test_test_case_to_dict_can_omit_input(session: Session) -> None:
    """The input can be withheld without duplicating the method."""
    problem = _make_problem()
    session.add(problem)
    session.commit()

    case = TestCase(
        problem_id=problem.id, ordinal=1, input="secret", expected_output="1"
    )
    session.add(case)
    session.commit()

    data = case.to_dict(include_input=False)
    assert "input" not in data
    assert data["expected_output"] == "1"


# --- Cross-model coherence ----------------------------------------------


def test_problem_detail_and_its_cases_are_consistent(session: Session) -> None:
    """Display samples and stored cases can be read together.

    The two collections are separate tables/columns by design (see the
    problem module docstring), so this checks the intended workflow still
    holds: a problem page shows ``problem.samples`` while judging walks the
    ``TestCase`` rows for the same problem.
    """
    problem = _make_problem(
        samples=[{"input": "3 5", "output": "8"}],
    )
    session.add(problem)
    session.commit()

    session.add_all(
        [
            TestCase(
                problem_id=problem.id,
                ordinal=1,
                input="3 5",
                expected_output="8",
                is_sample=True,
            ),
            TestCase(
                problem_id=problem.id,
                ordinal=2,
                input="-1 -1",
                expected_output="-2",
                is_sample=False,
            ),
        ]
    )
    session.commit()
    session.expunge_all()

    loaded = session.execute(select(Problem)).scalar_one()
    cases = (
        session.execute(
            select(TestCase)
            .where(TestCase.problem_id == loaded.id)
            .order_by(TestCase.ordinal)
        )
        .scalars()
        .all()
    )

    # The sample shown to the user is also a case the judge will run.
    assert loaded.samples[0]["input"] == cases[0].input
    assert loaded.samples[0]["output"] == cases[0].expected_output
    # And there is a hidden case the user never sees.
    assert len(cases) == 2
    assert cases[1].is_sample is False
