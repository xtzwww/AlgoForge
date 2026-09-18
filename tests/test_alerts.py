"""Tests for the teacher's attention list (阶段 6).

The property under test is not "the right students get flagged" — that is
a judgement no test can make. It is that **every alert carries the number
that fired it**, and that a rule does not fire on a student it should
not. A console that said "张三 需要关注" with nothing behind it would be
indistinguishable from a guess, and this project's whole position on the
process signal is that a mark has to be explainable.

The second thing worth pinning down is the cross-problem rule: three
attempts on one problem is persistence, not a pattern. The student-facing
nudge and this teacher-facing alert have to agree on that, because
nothing looks worse than a console contradicting what the student was
just told.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_user
from app.models.alerts import (
    ALERT_REPEATING,
    ALERT_SILENT,
    ALERT_STUCK,
    compute_alerts,
)
from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission
from app.records import SubmissionContext, record_submission
from app.sandbox.judge import JudgeResult, JudgeStatus


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


def _problem(session: Session, problem_id: int) -> None:
    if session.get(Problem, problem_id) is None:
        session.add(
            Problem(
                id=problem_id,
                title=f"P{problem_id}",
                knowledge_tags=["排序"],
                description="",
                samples=[],
            )
        )
        session.commit()


def _submit(
    session: Session,
    *,
    user_id: int,
    problem_id: int,
    status: JudgeStatus,
    code: str = "int main(){}",
    age_days: int = 0,
    now=None,
) -> int:
    """One submission, with a controllable age.

    ``created_at`` is written after the fact because the whole point of
    the silence rule is the gap between then and now, and a test cannot
    wait a week to produce one.
    """
    _problem(session, problem_id)
    submission_id = record_submission(
        JudgeResult(status=status),
        SubmissionContext(
            code=code, language="cpp", problem_id=problem_id, user_id=user_id
        ),
        session=session,
    )
    assert submission_id is not None

    if age_days:
        row = session.get(Submission, submission_id)
        assert row is not None
        base = now or row.created_at
        assert base is not None
        row.created_at = base - timedelta(days=age_days)
        session.commit()
    return submission_id


def _label(session: Session, submission_id: int, label: str) -> None:
    """Write the label an analysis would have written."""
    entry = session.scalar(
        select(MistakeBookEntry).where(MistakeBookEntry.submission_id == submission_id)
    )
    assert entry is not None, "no mistake-book entry was archived for this submission"
    entry.agent_error_type = label
    session.commit()


def _student(session: Session, username: str) -> int:
    user = create_user(session, username=username, password="a", display_name=username)
    return int(user.id)


# --- Nothing to report --------------------------------------------------


def test_a_student_who_is_doing_fine_produces_nothing(session: Session) -> None:
    alice = _student(session, "alice")
    _problem(session, 1)
    for i in range(6):
        _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.AC,
            code=f"int main(){{int x={i};}}",
        )
    assert compute_alerts(session) == []


def test_a_student_who_never_submitted_is_not_alerted(session: Session) -> None:
    """Absent from the alerts, present in the roster.

    Week one would otherwise alert on the entire class, and a list where
    everybody is on it is a list nobody reads.
    """
    _student(session, "alice")
    assert compute_alerts(session) == []


# --- Stuck --------------------------------------------------------------


def test_five_failures_in_a_row_raise_a_stuck_alert(session: Session) -> None:
    alice = _student(session, "alice")
    for i in range(5):
        _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int y={i};}}",
        )

    alerts = compute_alerts(session)
    assert len(alerts) == 1
    assert alerts[0].kind == ALERT_STUCK
    assert "5" in alerts[0].reason


def test_an_ac_inside_the_run_clears_it(session: Session) -> None:
    """"Getting there slowly" is not the same as "stuck"."""
    alice = _student(session, "alice")
    for i in range(4):
        _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int y={i};}}",
        )
    # The most recent one passes.
    _submit(
        session,
        user_id=alice,
        problem_id=1,
        status=JudgeStatus.AC,
        code="int main(){return 0;}",
    )

    assert [a.kind for a in compute_alerts(session)] == []


def test_fewer_attempts_than_the_threshold_is_not_stuck(session: Session) -> None:
    alice = _student(session, "alice")
    for i in range(4):
        _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int y={i};}}",
        )
    assert compute_alerts(session) == []


# --- Silent -------------------------------------------------------------


def test_a_student_who_went_quiet_raises_a_silent_alert(session: Session) -> None:
    alice = _student(session, "alice")
    _submit(
        session,
        user_id=alice,
        problem_id=1,
        status=JudgeStatus.AC,
        code="int main(){int z=1;}",
        age_days=9,
    )

    alerts = compute_alerts(session)
    assert len(alerts) == 1
    assert alerts[0].kind == ALERT_SILENT
    assert "9" in alerts[0].reason


def test_someone_active_yesterday_is_not_silent(session: Session) -> None:
    alice = _student(session, "alice")
    _submit(
        session,
        user_id=alice,
        problem_id=1,
        status=JudgeStatus.AC,
        code="int main(){int z=1;}",
        age_days=1,
    )
    assert compute_alerts(session) == []


# --- Repeating ----------------------------------------------------------


def test_the_same_cause_across_problems_raises_a_repeating_alert(
    session: Session,
) -> None:
    alice = _student(session, "alice")
    for problem_id in (1, 2, 3):
        submission_id = _submit(
            session,
            user_id=alice,
            problem_id=problem_id,
            status=JudgeStatus.WA,
            code=f"int main(){{int p={problem_id};}}",
        )
        _label(session, submission_id, "boundary")

    alerts = compute_alerts(session)
    assert len(alerts) == 1
    assert alerts[0].kind == ALERT_REPEATING
    # The count is the number of *problems*, which is the claim being made.
    assert "3" in alerts[0].reason


def test_three_attempts_on_one_problem_is_persistence_not_a_pattern(
    session: Session,
) -> None:
    """The cross-problem rule, shared with the student's own nudge.

    Retrying one hard problem five times is effort. The same mistake on
    three different problems is a gap. Alerting on the first would
    punish exactly the behaviour a teacher wants.
    """
    alice = _student(session, "alice")
    for i in range(3):
        submission_id = _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int q={i};}}",
        )
        _label(session, submission_id, "boundary")

    assert compute_alerts(session) == []


def test_unlabelled_mistakes_do_not_raise_a_repeating_alert(session: Session) -> None:
    """No diagnosis, no pattern claim — see the console's "未分析" group."""
    alice = _student(session, "alice")
    for problem_id in (1, 2, 3):
        _submit(
            session,
            user_id=alice,
            problem_id=problem_id,
            status=JudgeStatus.WA,
            code=f"int main(){{int r={problem_id};}}",
        )
    assert compute_alerts(session) == []


# --- Shape --------------------------------------------------------------


def test_one_student_can_raise_more_than_one_alert(session: Session) -> None:
    """Stuck and silent are different problems with different fixes."""
    alice = _student(session, "alice")
    for i in range(5):
        sid = _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int s={i};}}",
        )
        _label(session, sid, "boundary")
    # Age every one of them so the silence rule also fires.
    for row in session.execute(select(Submission)).scalars():
        assert row.created_at is not None
        row.created_at = row.created_at - timedelta(days=10)
    session.commit()

    kinds = {a.kind for a in compute_alerts(session)}
    assert {ALERT_STUCK, ALERT_SILENT} <= kinds


def test_the_most_actionable_alert_comes_first(session: Session) -> None:
    """Repeating names what to teach; silence might be a holiday."""
    alice = _student(session, "alice")

    for problem_id in (1, 2, 3):
        sid = _submit(
            session,
            user_id=alice,
            problem_id=problem_id,
            status=JudgeStatus.WA,
            code=f"int main(){{int t={problem_id};}}",
        )
        _label(session, sid, "boundary")
    for row in session.execute(select(Submission)).scalars():
        assert row.created_at is not None
        row.created_at = row.created_at - timedelta(days=10)
    session.commit()

    kinds = [a.kind for a in compute_alerts(session)]
    assert kinds.index(ALERT_REPEATING) < kinds.index(ALERT_SILENT)


def test_every_reason_carries_a_number(session: Session) -> None:
    """The whole point: a sentence with the evidence inside it."""
    alice = _student(session, "alice")
    for i in range(5):
        _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int u={i};}}",
        )
    sid = _submit(
        session,
        user_id=alice,
        problem_id=1,
        status=JudgeStatus.WA,
        code="int main(){int v=9;}",
        age_days=8,
    )
    _label(session, sid, "boundary")

    alerts = compute_alerts(session)
    assert alerts, "expected at least one alert"
    for alert in alerts:
        assert re.search(r"\d", alert.reason), alert.reason


def test_the_thresholds_can_be_tuned(session: Session) -> None:
    """A number that needs a deploy to change is a number nobody tunes."""
    alice = _student(session, "alice")
    for i in range(3):
        _submit(
            session,
            user_id=alice,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{int w={i};}}",
        )

    assert compute_alerts(session) == []
    assert len(compute_alerts(session, stuck_runs=3)) == 1
