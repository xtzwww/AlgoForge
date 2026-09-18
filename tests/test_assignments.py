"""Homework (阶段 4).

Two things are under test.

The first is the subtraction: "who has not done it" is the roster minus
the people who submitted, and there is no status column to keep in step.
That is the whole reason the schema has no per-student assignment row,
so the tests pin the behaviour that makes it safe — an attempt that
failed still counts as an attempt, and an unowned submission does not
silently mark somebody as present.

The second is the split between publishing and reading. Creating is
teacher-only; reading is open, because a student who cannot see the list
cannot do the work and the no-login path is how the client runs today.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import assignments as assignments_api
from app.api import auth as auth_api
from app.auth import create_user, issue_session
from app.main import app
from app.models.assignment import Assignment
from app.models.assignment_stats import compute_assignment_stats
from app.models.base import Base
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.user import ROLE_TEACHER
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


@pytest.fixture
def client(engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)
    monkeypatch.setattr(assignments_api, "get_session", _test_session)

    with TestClient(app) as c:
        yield c


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


def _publish(
    session: Session, problem_ids: list[int], title: str = "第一次作业"
) -> int:
    for pid in problem_ids:
        _problem(session, pid)
    row = Assignment(title=title, description="", problem_ids=list(problem_ids))
    session.add(row)
    session.commit()
    return int(row.id)


def _submit(
    session: Session, *, user_id: int | None, problem_id: int, status: JudgeStatus
) -> int:
    _problem(session, problem_id)
    submission_id = record_submission(
        JudgeResult(status=status),
        SubmissionContext(
            code="int main(){}", language="cpp", problem_id=problem_id, user_id=user_id
        ),
        session=session,
    )
    assert submission_id is not None
    return submission_id


def _student(session: Session, username: str) -> int:
    user = create_user(
        session, username=username, password="a", display_name=username
    )
    return int(user.id)


def _teacher_headers(session: Session) -> dict[str, str]:
    teacher = create_user(session, username="t", password="a", role=ROLE_TEACHER)
    return {"Authorization": f"Bearer {issue_session(session, teacher)}"}


# --- The subtraction ----------------------------------------------------


def test_nobody_missing_when_everybody_submitted(session: Session) -> None:
    alice = _student(session, "alice")
    assignment_id = _publish(session, [1])
    _submit(session, user_id=alice, problem_id=1, status=JudgeStatus.AC)

    stats = compute_assignment_stats(session, assignment_id)
    assert stats.students_who_submitted == 1
    assert stats.students_missing == []


def test_the_people_who_did_nothing_are_named(session: Session) -> None:
    """The only part of the screen anyone acts on."""
    alice = _student(session, "alice")
    _student(session, "bob")
    _student(session, "carol")
    assignment_id = _publish(session, [1])
    _submit(session, user_id=alice, problem_id=1, status=JudgeStatus.AC)

    stats = compute_assignment_stats(session, assignment_id)
    assert stats.students_who_submitted == 1
    assert stats.students_missing == ["bob", "carol"]


def test_a_failed_attempt_still_counts_as_submitting(session: Session) -> None:
    """An attempt that failed is not the same as no attempt.

    Counting only accepted work would put somebody who tried twelve
    times and never passed in the same list as somebody who never
    opened the problem.
    """
    alice = _student(session, "alice")
    assignment_id = _publish(session, [1])
    _submit(session, user_id=alice, problem_id=1, status=JudgeStatus.WA)

    stats = compute_assignment_stats(session, assignment_id)
    assert stats.students_who_submitted == 1
    assert stats.students_missing == []
    assert stats.problems[0].accepted == 0
    assert stats.problems[0].submissions == 1


def test_an_unowned_submission_does_not_count_as_a_student(
    session: Session,
) -> None:
    """Attribution is not inferred from the fact that a row exists."""
    _student(session, "alice")
    assignment_id = _publish(session, [1])
    _submit(session, user_id=None, problem_id=1, status=JudgeStatus.AC)

    stats = compute_assignment_stats(session, assignment_id)
    assert stats.students_who_submitted == 0
    assert stats.students_missing == ["alice"]


def test_problems_are_reported_in_the_order_the_teacher_listed_them(
    session: Session,
) -> None:
    """Not sorted — they will look for them in the order they chose."""
    assignment_id = _publish(session, [3, 1, 2])
    stats = compute_assignment_stats(session, assignment_id)
    assert [p.problem_id for p in stats.problems] == [3, 1, 2]


def test_an_untouched_assignment_is_a_valid_result(session: Session) -> None:
    """Zeros, not an error — it is the normal state on day one."""
    _student(session, "alice")
    assignment_id = _publish(session, [1])

    stats = compute_assignment_stats(session, assignment_id)
    assert stats.problems[0].submissions == 0
    assert stats.problems[0].accuracy == 0.0
    assert stats.students_who_submitted == 0


def test_a_missing_assignment_is_not_an_empty_one(session: Session) -> None:
    with pytest.raises(LookupError):
        compute_assignment_stats(session, 999)


# --- The routes ---------------------------------------------------------


def test_a_teacher_can_publish(client: TestClient, session: Session) -> None:
    _problem(session, 1)
    resp = client.post(
        "/api/v1/teacher/assignments",
        json={"title": "第一次作业", "problem_ids": [1]},
        headers=_teacher_headers(session),
    )
    assert resp.status_code == 201
    assert resp.json()["problem_ids"] == [1]


def test_an_unknown_problem_is_refused(client: TestClient, session: Session) -> None:
    """A typo must not publish homework pointing at nothing."""
    resp = client.post(
        "/api/v1/teacher/assignments",
        json={"title": "坏的", "problem_ids": [99]},
        headers=_teacher_headers(session),
    )
    assert resp.status_code == 404


def test_an_empty_assignment_is_refused(client: TestClient, session: Session) -> None:
    resp = client.post(
        "/api/v1/teacher/assignments",
        json={"title": "空的", "problem_ids": []},
        headers=_teacher_headers(session),
    )
    assert resp.status_code == 422


def test_a_bad_due_date_is_refused(client: TestClient, session: Session) -> None:
    """Silently becoming "no deadline" would be the opposite of what was asked."""
    _problem(session, 1)
    resp = client.post(
        "/api/v1/teacher/assignments",
        json={"title": "x", "problem_ids": [1], "due_at": "下周三"},
        headers=_teacher_headers(session),
    )
    assert resp.status_code == 422


def test_publishing_is_teacher_only(client: TestClient, session: Session) -> None:
    _problem(session, 1)
    student = create_user(session, username="stu", password="a")
    headers = {"Authorization": f"Bearer {issue_session(session, student)}"}

    resp = client.post(
        "/api/v1/teacher/assignments",
        json={"title": "x", "problem_ids": [1]},
        headers=headers,
    )
    assert resp.status_code == 403


def test_reading_the_list_is_open(client: TestClient, session: Session) -> None:
    """A student who cannot see the homework cannot do it."""
    _problem(session, 1)
    client.post(
        "/api/v1/teacher/assignments",
        json={"title": "第一次作业", "problem_ids": [1]},
        headers=_teacher_headers(session),
    )

    resp = client.get("/api/v1/assignments")
    assert resp.status_code == 200
    assert [a["title"] for a in resp.json()] == ["第一次作业"]


def test_the_stats_route_is_teacher_only(client: TestClient, session: Session) -> None:
    assignment_id = _publish(session, [1])
    student = create_user(session, username="stu2", password="a")
    headers = {"Authorization": f"Bearer {issue_session(session, student)}"}
    assert (
        client.get(
            f"/api/v1/teacher/assignments/{assignment_id}/stats", headers=headers
        ).status_code
        == 403
    )


def test_deleting_an_assignment_keeps_the_submissions(
    client: TestClient, session: Session
) -> None:
    """Un-publishing must not erase work anybody did."""
    alice = _student(session, "alice")
    assignment_id = _publish(session, [1])
    _submit(session, user_id=alice, problem_id=1, status=JudgeStatus.AC)

    resp = client.delete(
        f"/api/v1/teacher/assignments/{assignment_id}",
        headers=_teacher_headers(session),
    )
    assert resp.status_code == 204
    assert session.scalar(select(func.count()).select_from(Submission)) == 1


def test_a_past_due_date_is_reported_as_overdue(session: Session) -> None:
    """Overdue is computed once, here, not by each client."""
    row = Assignment(
        title="过期的",
        description="",
        problem_ids=[1],
        due_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
    )
    assert row.to_dict()["overdue"] is True


def test_no_deadline_is_not_overdue(session: Session) -> None:
    row = Assignment(title="随便做", description="", problem_ids=[1], due_at=None)
    assert row.to_dict()["overdue"] is False
    assert row.to_dict()["due_at"] is None
