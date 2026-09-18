"""Tests for 历史联动反馈 — 「这个错误你上次也犯过」(阶段 1).

The promise in the README is that feedback can reach back across
problems: a student who has mis-handled 边界条件 on four different
tasks should be told so, instead of meeting 边界条件 four separate
times as if each were news.

The two tests that matter most are the two *exclusions* — a repeat on
the same problem, and another student's history. Both are cases where a
plausible-looking implementation returns a number, and both produce a
number that would be read as a fact about the student looking at the
screen. A count that silently includes someone else's mistakes is worse
than no count at all, because it is invisible: the student has no way to
notice the attribution is wrong.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.recurrence import (
    SAMPLE_LIMIT,
    Recurrence,
    count_distinct_problems,
    find_recurrence,
)
from app.models.submission import Submission


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _user(session: Session, name: str, display: str = "") -> int:
    from app.auth import create_user

    user = create_user(session, username=name, password="pw", display_name=display)
    return user.id


def _problem(session: Session, problem_id: int) -> Problem:
    """Ensure the problem row exists, once.

    Idempotent rather than always inserting: several tests file two
    mistakes against the same problem, and an unconditional insert there
    hits the primary key and fails for a reason that has nothing to do
    with what is under test.
    """
    existing = session.get(Problem, problem_id)
    if existing is not None:
        return existing
    row = Problem(
        id=problem_id,
        title=f"P{problem_id}",
        knowledge_tags=["排序"],
        description="",
        samples=[],
    )
    session.add(row)
    session.commit()
    return row


def _mistake(
    session: Session,
    *,
    user_id: int | None,
    problem_id: int,
    error_type: str | None,
    code: str = "int main(){}",
) -> int:
    """File one analysed mistake-book entry directly.

    Written rather than recorded through ``record_submission``: these
    tests are about the lookup, and routing them through the judge would
    make each one wait on a real compile.
    """
    _problem(session, problem_id)
    submission = Submission(
        problem_id=problem_id,
        user_id=user_id,
        status="WA",
        code=code,
        language="cpp",
    )
    session.add(submission)
    session.flush()
    entry = MistakeBookEntry(
        submission_id=submission.id,
        problem_id=problem_id,
        agent_error_type=error_type,
        error_cause="",
        knowledge_tags=["排序"],
        # Distinct per entry so the dedupe path is never accidentally
        # exercised by two rows sharing one fingerprint.
        error_cause_fingerprint=f"{problem_id}:{code}",
    )
    session.add(entry)
    session.commit()
    return entry.id


# --- what a first occurrence looks like --------------------------------


def test_a_first_occurrence_is_zero_not_missing(session: Session) -> None:
    """`count == 0` is the answer, not an absence of one."""
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.count == 0
    assert found.problem_ids == []
    assert found.last_at is None


def test_the_summary_is_empty_for_a_first_occurrence(session: Session) -> None:
    """Nothing is said the first time.

    A line that always renders — even one reading "this is your first
    time" — teaches the student to skip the region, including on the day
    it finally has something to say.
    """
    assert Recurrence(label="boundary", count=0).summary() == ""


def test_an_empty_history_is_zero(session: Session) -> None:
    alice = _user(session, "alice")
    assert (
        find_recurrence(
            session, user_id=alice, error_type="boundary", problem_id=1
        ).count
        == 0
    )


# --- counting ----------------------------------------------------------


def test_the_same_cause_on_another_problem_counts_once(session: Session) -> None:
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary")
    _mistake(session, user_id=alice, problem_id=2, error_type="boundary")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=3
    )
    assert found.count == 2


def test_the_summary_names_the_count(session: Session) -> None:
    """The sentence says "N times before", not "N times" — see below."""
    text = Recurrence(label="boundary", count=2).summary()
    assert "2 次" in text
    # Three occurrences in total, of which two came earlier. A student
    # reading only the second number would think they had been told
    # about a repeat that includes the mistake on screen right now.
    assert "共 3 次" in text


def test_repeats_on_one_problem_count_once(session: Session) -> None:
    """Two failures on the same problem are one occurrence, not two.

    Retrying a problem is ordinary; the number is meant to mean "this
    keeps happening on problems you have not seen before".
    """
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary", code="a")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary", code="b")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=2
    )
    assert found.count == 1


def test_the_current_problem_is_excluded(session: Session) -> None:
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary")

    # Analysing problem 1 *now*: its own earlier attempt is not a prior
    # occurrence on another problem.
    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.count == 0


def test_a_different_cause_does_not_count(session: Session) -> None:
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=2, error_type="off_by_one")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.count == 0


def test_an_unlabelled_entry_does_not_count(session: Session) -> None:
    """An entry whose analysis never landed is not evidence of a cause."""
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=2, error_type=None)

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.count == 0


# --- the two exclusions that matter most -------------------------------


def test_another_students_history_is_not_counted(session: Session) -> None:
    """The count is about the student looking at the screen.

    A lookup that forgot the owner would still return a number — a
    plausible one, from a real row — and nobody would be able to tell it
    referred to somebody else.
    """
    alice = _user(session, "alice")
    bob = _user(session, "bob")
    _mistake(session, user_id=bob, problem_id=2, error_type="boundary")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.count == 0


def test_an_unowned_submission_has_no_history(session: Session) -> None:
    """Ownerless is not "everybody's".

    Everything recorded before the user system existed is unowned, and
    counting all of it as one student's past would attribute a stranger's
    mistakes to whoever happens to be looking.
    """
    _mistake(session, user_id=None, problem_id=2, error_type="boundary")

    found = find_recurrence(
        session, user_id=None, error_type="boundary", problem_id=1
    )
    assert found.count == 0
    assert found.summary() == ""


# --- the shape of the answer -------------------------------------------


def test_the_entry_being_annotated_does_not_count_itself(session: Session) -> None:
    """Off-by-one guard.

    The entry is committed with its label *before* the lookup runs, so
    without `exclude_entry_id` every count would include the mistake the
    student is looking at — and "once" would be reported as "twice".
    """
    alice = _user(session, "alice")
    entry_id = _mistake(session, user_id=alice, problem_id=1, error_type="boundary")

    # Queried with no current problem — the shape a re-analysis takes,
    # where the entry already carries its label and nothing else is
    # being excluded. Here the entry would match itself.
    without = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=None
    )
    with_id = find_recurrence(
        session,
        user_id=alice,
        error_type="boundary",
        problem_id=None,
        exclude_entry_id=entry_id,
    )
    assert without.count == 1
    assert with_id.count == 0


def test_the_sample_is_capped(session: Session) -> None:
    """Enough problem ids to say "where", not enough to be homework."""
    alice = _user(session, "alice")
    for problem_id in range(2, 2 + SAMPLE_LIMIT + 3):
        _mistake(session, user_id=alice, problem_id=problem_id, error_type="boundary")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.count == SAMPLE_LIMIT + 3
    assert len(found.problem_ids) == SAMPLE_LIMIT


def test_the_most_recent_problem_comes_first(session: Session) -> None:
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=5, error_type="boundary")
    _mistake(session, user_id=alice, problem_id=6, error_type="boundary")
    _mistake(session, user_id=alice, problem_id=7, error_type="boundary")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.problem_ids == [7, 6, 5]


def test_last_at_is_the_most_recent_occurrence(session: Session) -> None:
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=5, error_type="boundary")

    found = find_recurrence(
        session, user_id=alice, error_type="boundary", problem_id=1
    )
    assert found.last_at is not None


def test_to_dict_carries_the_sentence(session: Session) -> None:
    data = Recurrence(label="boundary", count=2, problem_ids=[4, 5]).to_dict()
    assert data["count"] == 2
    assert data["problem_ids"] == [4, 5]
    assert data["summary"]
    # The label is echoed so the caller does not have to carry it
    # alongside the answer it asked for.
    assert data["label"] == "boundary"


def test_count_distinct_problems_ignores_the_current_problem(
    session: Session,
) -> None:
    """The console's question is the whole history, not "before now"."""
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary")
    _mistake(session, user_id=alice, problem_id=2, error_type="boundary")

    assert count_distinct_problems(session, user_id=alice, error_type="boundary") == 2
    assert (
        count_distinct_problems(session, user_id=alice, error_type="off_by_one") == 0
    )


# --- the wiring ---------------------------------------------------------


def test_the_diagnosis_carries_the_history(session: Session) -> None:
    """`agent_done` reports the count, or zero — never omits the key.

    Checked at the seam rather than through a judge run: what is under
    test is that the payload the client reads has the field at all. A
    key that appears only when there *is* a history makes "no history"
    and "the field was renamed" look identical to the client.
    """
    from unittest.mock import patch

    from app.llm.gateway import Completion
    from app.models.mistake_book import MistakeBookEntry as Entry
    from app.records import _analyse_and_annotate
    from app.sandbox.judge import JudgeResult, JudgeStatus, TestCaseResult

    alice = _user(session, "alice")
    _problem(session, 1)
    _mistake(session, user_id=alice, problem_id=2, error_type="overflow")

    submission = Submission(
        problem_id=1, user_id=alice, status="WA", code="int a,b;", language="cpp"
    )
    session.add(submission)
    session.flush()
    entry = Entry(
        submission_id=submission.id,
        problem_id=1,
        error_cause="",
        knowledge_tags=[],
        error_cause_fingerprint="wiring",
    )
    session.add(entry)
    session.commit()

    result = JudgeResult(
        status=JudgeStatus.WA,
        test_cases=[
            TestCaseResult(
                status=JudgeStatus.WA,
                input="3 7",
                expected_output="21",
                actual_output="0",
            )
        ],
    )
    from app.records import SubmissionContext

    payload = (
        '{"error_type":"overflow","error_detail":"溢出","hint":"用 long long",'
        '"suggested_fix":"先转 64 位"}'
    )
    with patch(
        "app.agent.tool_caller.complete",
        side_effect=[Completion(content=payload, finish_reason="stop")],
    ):
        done = _analyse_and_annotate(
            entry,
            result,
            SubmissionContext(code="int a,b;", language="cpp", problem_id=1,
                              user_id=alice),
        )

    assert done is not None
    assert done["recurrence"]["count"] == 1
    assert "1 次" in done["recurrence"]["summary"]


def test_a_failed_analysis_still_carries_the_field(session: Session) -> None:
    """A diagnosis that never landed says zero, not nothing."""
    from app.models.mistake_book import MistakeBookEntry as Entry
    from app.records import SubmissionContext, _analyse_and_annotate
    from app.sandbox.judge import JudgeResult, JudgeStatus

    _problem(session, 1)
    submission = Submission(
        problem_id=1, user_id=None, status="CE", code="int a,b;", language="cpp"
    )
    session.add(submission)
    session.flush()
    entry = Entry(
        submission_id=submission.id,
        problem_id=1,
        error_cause="",
        knowledge_tags=[],
        error_cause_fingerprint="ce",
    )
    session.add(entry)
    session.commit()

    # A CE has no failing case, so this returns None rather than a dict —
    # the payload the client sees comes from the failure branch, which is
    # covered by the sibling test in test_agent_analysis_recording.py.
    # What is asserted here is the simpler half: no history is claimed
    # when there is no owner to attribute it to.
    found = find_recurrence(session, user_id=None, error_type="", problem_id=1)
    assert found.to_dict()["count"] == 0

    assert (
        _analyse_and_annotate(
            entry,
            JudgeResult(status=JudgeStatus.CE),
            SubmissionContext(code="int a,b;", language="cpp", problem_id=1),
        )
        is None
    )


def test_entries_are_listed_in_order(session: Session) -> None:
    """Sanity check on the fixture, not on the feature.

    `_mistake` writes rows the lookup then reads; if it silently failed
    to persist, every count above would be zero for the wrong reason.
    """
    alice = _user(session, "alice")
    _mistake(session, user_id=alice, problem_id=1, error_type="boundary")
    _mistake(session, user_id=alice, problem_id=2, error_type="boundary")
    assert len(list(session.execute(select(MistakeBookEntry)).scalars())) == 2
