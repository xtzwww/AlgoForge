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
from conftest import TEST_SIDECAR_HEADERS


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

    with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
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




# --- 复制作业（阶段 5）-------------------------------------------------


def test_duplicate_copies_the_problem_set_verbatim(
    session: Session, client: TestClient
) -> None:
    """复制的是题目集合本身。

    作业的复用方式是「同一份题再用一次」，而重新手打编号正是错题混进来的
    时刻——复制一份不该经过人手。
    """
    source = _publish(session, [11, 12, 13], title="第一次作业")
    headers = _teacher_headers(session)

    resp = client.post(
        f"/api/v1/teacher/assignments/{source}/duplicate", json={}, headers=headers
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["problem_ids"] == [11, 12, 13]
    assert body["title"] == "第一次作业（副本）"
    assert body["id"] != source


def test_duplicate_keeps_the_assistance_level(
    session: Session, client: TestClient
) -> None:
    """档位跟着复制。

    这一条是刻意的：如果复制时落回默认档，一份一档的作业复制出来就悄悄
    允许了 AI 对话——「比原来松」是唯一要紧的方向，所以宁可保守。
    """
    _problem(session, 21)
    row = Assignment(
        title="严格作业", description="", problem_ids=[21], assistance_level=1
    )
    session.add(row)
    session.commit()

    resp = client.post(
        f"/api/v1/teacher/assignments/{row.id}/duplicate",
        json={},
        headers=_teacher_headers(session),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["assistance_level"] == 1


def test_duplicate_can_rename_and_redate(session: Session, client: TestClient) -> None:
    """改名字和改截止时间是复制之后必然要做的两件事。

    不传则沿用原标题、清掉截止时间——「同一份题换个班再发一次」是另一半用法，
    它想要的就是原名和无截止。
    """
    source = _publish(session, [22])
    headers = _teacher_headers(session)

    resp = client.post(
        f"/api/v1/teacher/assignments/{source}/duplicate",
        json={"title": "第二次作业", "due_at": "2030-01-01T10:00"},
        headers=headers,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "第二次作业"
    assert body["due_at"].startswith("2030-01-01")


def test_duplicate_of_a_missing_assignment_is_404(
    session: Session, client: TestClient
) -> None:
    resp = client.post(
        "/api/v1/teacher/assignments/9999/duplicate",
        json={},
        headers=_teacher_headers(session),
    )
    assert resp.status_code == 404


# --- 学生成绩单（阶段 5）------------------------------------------------


def test_report_lists_every_assignment_including_the_ones_not_attempted(
    session: Session, client: TestClient
) -> None:
    """**没交也要出现在成绩单上。**

    这条是这个接口存在的唯一理由：错题本按定义看不到「一道题都没做」的
    学生——他没有错题。而恰恰是这样的学生最需要被看见。
    """
    student = _student(session, "s1")
    _publish(session, [31], title="做过的")
    _publish(session, [32], title="没做的")
    _submit(session, user_id=student, problem_id=31, status=JudgeStatus.AC)

    resp = client.get(
        f"/api/v1/teacher/students/{student}/report", headers=_teacher_headers(session)
    )

    assert resp.status_code == 200, resp.text
    titles = [r["title"] for r in resp.json()["rows"]]
    assert "做过的" in titles
    assert "没做的" in titles


def test_report_distinguishes_not_attempted_from_answered_wrong(
    session: Session, client: TestClient
) -> None:
    """「没做」和「做了但全错」必须是两种不同的读数。

    都用 0.0 表示的话，老师分不出该催谁、该辅导谁。所以没提交是 null，
    提交过但没通过是 0.0。
    """
    student = _student(session, "s1")
    _publish(session, [41], title="全错")
    _publish(session, [42], title="没碰")
    _submit(session, user_id=student, problem_id=41, status=JudgeStatus.WA)

    resp = client.get(
        f"/api/v1/teacher/students/{student}/report", headers=_teacher_headers(session)
    )
    by_title = {r["title"]: r for r in resp.json()["rows"]}

    assert by_title["全错"]["submissions"] == 1
    assert by_title["全错"]["progress"] == 0.0
    assert by_title["没碰"]["submissions"] == 0
    assert by_title["没碰"]["progress"] is None


def test_report_counts_progress_per_problem_not_per_submission(
    session: Session, client: TestClient
) -> None:
    """同一题交十次仍然只算一道题。

    按提交次数算的话，反复重交会把这个人的进度推上去——而这恰恰是
    最需要帮助的学生的行为模式。
    """
    student = _student(session, "s1")
    _publish(session, [51, 52], title="两题")
    _submit(session, user_id=student, problem_id=51, status=JudgeStatus.AC)
    for _ in range(5):
        _submit(
            session, user_id=student, problem_id=52, status=JudgeStatus.WA
        )

    resp = client.get(
        f"/api/v1/teacher/students/{student}/report", headers=_teacher_headers(session)
    )
    row = resp.json()["rows"][0]

    assert row["problem_count"] == 2
    assert row["submissions"] == 6
    assert row["accepted"] == 1
    # 1 / 2，不是 1 / 6
    assert row["progress"] == 0.5


def test_report_completed_counts_assignments_with_any_submission(
    session: Session, client: TestClient
) -> None:
    student = _student(session, "s1")
    _publish(session, [61], title="A")
    _publish(session, [62], title="B")
    _publish(session, [63], title="C")
    _submit(session, user_id=student, problem_id=61, status=JudgeStatus.WA)

    resp = client.get(
        f"/api/v1/teacher/students/{student}/report", headers=_teacher_headers(session)
    )
    body = resp.json()

    assert body["total_assignments"] == 3
    # 交错了也算交过：这是「有没有参与」，不是「做没做对」。
    assert body["completed"] == 1


def test_report_ignores_other_students_submissions(
    session: Session, client: TestClient
) -> None:
    """别人的提交不能算到这个学生头上。"""
    mine = _student(session, "s1")
    theirs = _student(session, "s2")
    _publish(session, [71], title="作业")
    _submit(session, user_id=theirs, problem_id=71, status=JudgeStatus.AC)

    resp = client.get(
        f"/api/v1/teacher/students/{mine}/report", headers=_teacher_headers(session)
    )
    row = resp.json()["rows"][0]

    assert row["submissions"] == 0
    assert row["progress"] is None


def test_report_of_a_missing_student_is_404(
    session: Session, client: TestClient
) -> None:
    resp = client.get(
        "/api/v1/teacher/students/9999/report", headers=_teacher_headers(session)
    )
    assert resp.status_code == 404


def test_report_is_teacher_only(session: Session, client: TestClient) -> None:
    student = _student(session, "s1")
    _publish(session, [81], title="作业")

    # 没有 Authorization：只有进程凭据，不是老师。
    resp = client.get(f"/api/v1/teacher/students/{student}/report")

    assert resp.status_code in (401, 403)
