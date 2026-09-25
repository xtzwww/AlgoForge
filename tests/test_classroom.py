"""Classes and the student's side of homework (B2).

Three things are under test, in descending order of how much damage a
regression would do.

**The boundary.** A class is the first thing in this codebase that one
user owns and another must not see. Two distinct refusals carry that:

* a **student** reaching a teacher route gets 403 — they are known and
  simply not allowed there;
* a **teacher** reaching another teacher's class gets 404 — not 403,
  because a 403 would confirm the class exists and one teacher has no
  business knowing the shape of another's roster.

The second one is the easy one to get wrong, and getting it wrong does
not look wrong: the code returns *a* refusal either way.

**Joining.** A student types a code. What must hold is that a wrong code
changes nothing, a right one changes exactly one thing, and repeating it
changes nothing further. The last part matters more than it looks —
"join" is a button people press twice, and a second press that errors
sends them hunting for a mistake they did not make.

**The subtraction, again.** ``/my/assignments`` shows homework set for
the student's classes plus homework set for everyone, and nothing else.
That exclusion is the entire reason to assign something to a class
rather than to the whole school, so it is asserted directly rather than
inferred from the positive case.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import assignments as assignments_api
from app.api import auth as auth_api
from app.api import classroom as classroom_api
from app.auth import create_user, issue_session
from app.main import app
from app.models.assignment import Assignment
from app.models.base import Base
from app.models.classroom import INVITE_CODE_LENGTH, ClassMember, Classroom
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.user import ROLE_STUDENT, ROLE_TEACHER
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
    """A client whose auth, classroom and homework routes share one engine.

    All three are redirected, not just the one under test: a class is
    only interesting through what it does to the *other* two, so a test
    that pointed the classroom routes at the test database and left the
    rest on the real one would be asserting against two different worlds.
    """
    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)
    monkeypatch.setattr(classroom_api, "get_session", _test_session)
    monkeypatch.setattr(assignments_api, "get_session", _test_session)

    with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
        yield c


def _user(session: Session, username: str, role: str = ROLE_STUDENT):
    user = create_user(
        session, username=username, password="a", display_name=username, role=role
    )
    session.commit()
    return user


def _as(session: Session, user) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_session(session, user)}"}


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


def _submit(session: Session, *, user_id: int, problem_id: int, status: JudgeStatus):
    _problem(session, problem_id)
    submission_id = record_submission(
        JudgeResult(status=status),
        SubmissionContext(
            code="int main(){}",
            language="cpp",
            problem_id=problem_id,
            user_id=user_id,
        ),
        session=session,
    )
    assert submission_id is not None
    return submission_id


def _make_class(client: TestClient, headers: dict[str, str], name: str = "软件 2 班"):
    resp = client.post(
        "/api/v1/teacher/classrooms", json={"name": name}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _publish(
    session: Session,
    problem_ids: list[int],
    *,
    title: str = "第一次作业",
    class_id: int | None = None,
) -> int:
    for pid in problem_ids:
        _problem(session, pid)
    row = Assignment(
        title=title, description="", problem_ids=list(problem_ids), class_id=class_id
    )
    session.add(row)
    session.commit()
    return int(row.id)


# --- The boundary -------------------------------------------------------


def test_a_logged_out_request_is_refused(client: TestClient) -> None:
    assert client.get("/api/v1/classrooms/mine").status_code == 401


def test_a_student_cannot_create_a_class(client: TestClient, session: Session) -> None:
    student = _user(session, "alice")
    resp = client.post(
        "/api/v1/teacher/classrooms",
        json={"name": "偷偷建的班"},
        headers=_as(session, student),
    )
    assert resp.status_code == 403


def test_a_student_cannot_read_a_roster(client: TestClient, session: Session) -> None:
    student = _user(session, "alice")
    resp = client.get(
        "/api/v1/teacher/classrooms/1/members", headers=_as(session, student)
    )
    assert resp.status_code == 403


def test_another_teachers_class_is_not_found(
    client: TestClient, session: Session
) -> None:
    """404, not 403 — a 403 would confirm the class exists.

    Asserted on the roster *and* on the delete, because they are separate
    routes and a fix applied to one is not a fix applied to both.
    """
    owner = _user(session, "t1", ROLE_TEACHER)
    stranger = _user(session, "t2", ROLE_TEACHER)
    room = _make_class(client, _as(session, owner))

    headers = _as(session, stranger)
    assert (
        client.get(
            f"/api/v1/teacher/classrooms/{room['id']}/members", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/teacher/classrooms/{room['id']}", headers=headers
        ).status_code
        == 404
    )
    # And the class is still there afterwards.
    assert session.get(Classroom, room["id"]) is not None


def test_another_teachers_class_cannot_be_borrowed_by_a_new_assignment(
    client: TestClient, session: Session
) -> None:
    """Ownership is checked on write, not only on read.

    A teacher who could set homework for someone else's class could put
    work in front of students they do not teach, and the roster would
    never explain where it came from.
    """
    owner = _user(session, "t1", ROLE_TEACHER)
    stranger = _user(session, "t2", ROLE_TEACHER)
    room = _make_class(client, _as(session, owner))

    resp = client.post(
        "/api/v1/teacher/assignments",
        json={"title": "越权作业", "problem_ids": [1], "class_id": room["id"]},
        headers=_as(session, stranger),
    )
    assert resp.status_code == 404


# --- Creating and the invite code ---------------------------------------


def test_a_new_class_comes_back_with_an_invite_code(
    client: TestClient, session: Session
) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    room = _make_class(client, _as(session, teacher), name="软件 2 班")

    assert room["name"] == "软件 2 班"
    assert room["member_count"] == 0
    assert len(room["invite_code"]) == INVITE_CODE_LENGTH
    # The alphabet deliberately excludes I/O/0/1: the code gets read
    # aloud and written on a whiteboard, and those are the pairs people
    # transcribe wrongly.
    assert not set(room["invite_code"]) & set("IO01")


def test_two_classes_do_not_share_a_code(client: TestClient, session: Session) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    headers = _as(session, teacher)
    first = _make_class(client, headers, name="A")
    second = _make_class(client, headers, name="B")
    assert first["invite_code"] != second["invite_code"]


def test_a_blank_name_is_rejected(client: TestClient, session: Session) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    resp = client.post(
        "/api/v1/teacher/classrooms",
        json={"name": "   "},
        headers=_as(session, teacher),
    )
    assert resp.status_code == 422


def test_a_teacher_sees_only_their_own_classes(
    client: TestClient, session: Session
) -> None:
    mine = _user(session, "t1", ROLE_TEACHER)
    theirs = _user(session, "t2", ROLE_TEACHER)
    _make_class(client, _as(session, mine), name="我的班")
    _make_class(client, _as(session, theirs), name="别人的班")

    body = client.get("/api/v1/teacher/classrooms", headers=_as(session, mine)).json()
    assert [c["name"] for c in body] == ["我的班"]


# --- Joining ------------------------------------------------------------


def test_a_student_joins_with_the_code(client: TestClient, session: Session) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    student = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))
    headers = _as(session, student)

    resp = client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": room["invite_code"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["already_member"] is False
    assert body["classroom"]["id"] == room["id"]
    assert body["classroom"]["member_count"] == 1

    mine = client.get("/api/v1/classrooms/mine", headers=headers).json()
    assert [c["id"] for c in mine] == [room["id"]]


def test_joining_twice_is_not_an_error(client: TestClient, session: Session) -> None:
    """The button gets pressed twice; the second press must be a no-op.

    Not merely "does not 4xx": the roster must not grow a duplicate row,
    because a duplicated membership is a student counted twice in every
    number the console shows.
    """
    teacher = _user(session, "t1", ROLE_TEACHER)
    student = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))
    headers = _as(session, student)

    payload = {"invite_code": room["invite_code"]}
    client.post("/api/v1/classrooms/join", json=payload, headers=headers)
    second = client.post("/api/v1/classrooms/join", json=payload, headers=headers)

    assert second.status_code == 200
    assert second.json()["already_member"] is True
    assert second.json()["classroom"]["member_count"] == 1
    members = session.execute(select(ClassMember)).scalars().all()
    assert len(members) == 1


def test_the_code_is_case_insensitive(client: TestClient, session: Session) -> None:
    """A lowercase code is the same code with the shift key up."""
    teacher = _user(session, "t1", ROLE_TEACHER)
    student = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))

    resp = client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": room["invite_code"].lower()},
        headers=_as(session, student),
    )
    assert resp.status_code == 200
    assert resp.json()["classroom"]["id"] == room["id"]


def test_surrounding_spaces_are_ignored(client: TestClient, session: Session) -> None:
    """Pasted codes arrive with whitespace; that is not the student's error."""
    teacher = _user(session, "t1", ROLE_TEACHER)
    student = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))

    resp = client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": f"  {room['invite_code']}  "},
        headers=_as(session, student),
    )
    assert resp.status_code == 200


def test_an_unknown_code_changes_nothing(client: TestClient, session: Session) -> None:
    student = _user(session, "alice")
    resp = client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": "ZZZZZZ"},
        headers=_as(session, student),
    )
    assert resp.status_code == 404
    assert session.execute(select(ClassMember)).scalars().all() == []


def test_a_teacher_cannot_join_a_class(client: TestClient, session: Session) -> None:
    """Membership means "this person is taught here".

    Letting a teacher in would put them on their own roster and count
    them, forever, as a student who never submits — which is the exact
    signal the console exists to surface.
    """
    owner = _user(session, "t1", ROLE_TEACHER)
    other = _user(session, "t2", ROLE_TEACHER)
    room = _make_class(client, _as(session, owner))

    resp = client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": room["invite_code"]},
        headers=_as(session, other),
    )
    assert resp.status_code == 403
    assert session.execute(select(ClassMember)).scalars().all() == []


def test_a_teacher_asking_for_their_classes_gets_an_empty_list(
    client: TestClient, session: Session
) -> None:
    """Not a 403.

    The shell renders this page for everyone, and a teacher calling it
    gets the truth about their own memberships — which is none. A 403
    here would put an error card in front of a page that is merely
    irrelevant to them.
    """
    teacher = _user(session, "t1", ROLE_TEACHER)
    resp = client.get("/api/v1/classrooms/mine", headers=_as(session, teacher))
    assert resp.status_code == 200
    assert resp.json() == []


# --- The roster ---------------------------------------------------------


def test_the_roster_names_who_joined(client: TestClient, session: Session) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    alice = _user(session, "alice")
    bob = _user(session, "bob")
    room = _make_class(client, _as(session, teacher))

    for user in (alice, bob):
        client.post(
            "/api/v1/classrooms/join",
            json={"invite_code": room["invite_code"]},
            headers=_as(session, user),
        )

    body = client.get(
        f"/api/v1/teacher/classrooms/{room['id']}/members",
        headers=_as(session, teacher),
    ).json()
    assert [m["username"] for m in body] == ["alice", "bob"]
    assert [m["display_name"] for m in body] == ["alice", "bob"]


def test_removing_a_member_leaves_their_work_alone(
    client: TestClient, session: Session
) -> None:
    """Being taken off a roster is not the same as never having worked.

    The submissions stay attached to the student. If removing a
    membership deleted them, the console's per-student numbers would
    change retroactively and nothing on screen could explain why.
    """
    teacher = _user(session, "t1", ROLE_TEACHER)
    alice = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))
    client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": room["invite_code"]},
        headers=_as(session, alice),
    )
    _submit(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)

    resp = client.delete(
        f"/api/v1/teacher/classrooms/{room['id']}/members/{alice.id}",
        headers=_as(session, teacher),
    )
    assert resp.status_code == 204

    assert session.execute(select(ClassMember)).scalars().all() == []
    assert session.execute(select(Submission)).scalars().all() != []


def test_removing_someone_who_never_joined_is_a_404(
    client: TestClient, session: Session
) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    alice = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))

    resp = client.delete(
        f"/api/v1/teacher/classrooms/{room['id']}/members/{alice.id}",
        headers=_as(session, teacher),
    )
    assert resp.status_code == 404


def test_deleting_a_class_unassigns_its_homework(
    client: TestClient, session: Session
) -> None:
    """The homework survives; only its audience widens.

    Deleting the class would otherwise erase the record of work students
    actually did, and no screen could say where it went.
    """
    teacher = _user(session, "t1", ROLE_TEACHER)
    alice = _user(session, "alice")
    room = _make_class(client, _as(session, teacher))
    client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": room["invite_code"]},
        headers=_as(session, alice),
    )
    assignment_id = _publish(session, [1], class_id=room["id"])

    resp = client.delete(
        f"/api/v1/teacher/classrooms/{room['id']}", headers=_as(session, teacher)
    )
    assert resp.status_code == 204

    session.expire_all()
    row = session.get(Assignment, assignment_id)
    assert row is not None, "the homework was deleted along with the class"
    assert row.class_id is None
    assert session.get(Classroom, room["id"]) is None
    assert session.execute(select(ClassMember)).scalars().all() == []


# --- The student's homework list ----------------------------------------


def test_my_assignments_shows_class_homework_and_public_homework(
    client: TestClient, session: Session
) -> None:
    """And, crucially, not another class's homework.

    That exclusion is the whole reason ``class_id`` exists: without it
    the class picker on the teacher's screen would be decoration.
    """
    teacher = _user(session, "t1", ROLE_TEACHER)
    alice = _user(session, "alice")
    mine = _make_class(client, _as(session, teacher), name="我的班")
    other = _make_class(client, _as(session, teacher), name="别人的班")

    _publish(session, [1], title="全班可见", class_id=None)
    _publish(session, [2], title="我的班作业", class_id=mine["id"])
    _publish(session, [3], title="别班作业", class_id=other["id"])

    client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": mine["invite_code"]},
        headers=_as(session, alice),
    )

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert sorted(a["title"] for a in body) == ["全班可见", "我的班作业"]


def test_a_student_in_no_class_still_sees_the_public_homework(
    client: TestClient, session: Session
) -> None:
    """The pre-classes rows must not vanish for existing accounts."""
    alice = _user(session, "alice")
    _publish(session, [1], title="老作业", class_id=None)

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert [a["title"] for a in body] == ["老作业"]


def test_the_class_name_comes_along_for_grouping(
    client: TestClient, session: Session
) -> None:
    teacher = _user(session, "t1", ROLE_TEACHER)
    alice = _user(session, "alice")
    room = _make_class(client, _as(session, teacher), name="软件 2 班")
    _publish(session, [1], title="作业", class_id=room["id"])
    client.post(
        "/api/v1/classrooms/join",
        json={"invite_code": room["invite_code"]},
        headers=_as(session, alice),
    )

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert body[0]["classroom_name"] == "软件 2 班"
    assert body[0]["class_id"] == room["id"]


def test_my_assignments_ticks_the_problems_actually_passed(
    client: TestClient, session: Session
) -> None:
    """A wrong answer is not a solved problem.

    A second AC on the same problem is not a second problem, either —
    which is why the count is a ``max`` over a 1/0 case rather than a sum.
    """
    alice = _user(session, "alice")
    _publish(session, [1, 2, 3], title="作业")

    _submit(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)
    _submit(session, user_id=alice.id, problem_id=2, status=JudgeStatus.WA)
    _submit(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert body[0]["solved_problem_ids"] == [1]
    assert body[0]["solved"] == 1
    assert body[0]["submissions"] == 3
    assert body[0]["problem_count"] == 3


def test_progress_is_unknown_before_any_attempt(
    client: TestClient, session: Session
) -> None:
    """``None`` and ``0.0`` are different things on screen.

    "Has not started" and "started and passed none of them" call for
    different words, so the endpoint must not collapse them.
    """
    alice = _user(session, "alice")
    _publish(session, [1, 2], title="作业")

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert body[0]["progress"] is None
    assert body[0]["submissions"] == 0


def test_progress_counts_once_the_student_starts(
    client: TestClient, session: Session
) -> None:
    alice = _user(session, "alice")
    _publish(session, [1, 2], title="作业")
    _submit(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)
    _submit(session, user_id=alice.id, problem_id=2, status=JudgeStatus.WA)

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert body[0]["progress"] == pytest.approx(0.5)


def test_another_students_solved_problems_are_not_mine(
    client: TestClient, session: Session
) -> None:
    alice = _user(session, "alice")
    bob = _user(session, "bob")
    _publish(session, [1], title="作业")
    _submit(session, user_id=bob.id, problem_id=1, status=JudgeStatus.AC)

    body = client.get("/api/v1/my/assignments", headers=_as(session, alice)).json()
    assert body[0]["solved_problem_ids"] == []
    assert body[0]["progress"] is None


def test_a_teacher_gets_only_the_public_homework(
    client: TestClient, session: Session
) -> None:
    """A teacher is in no class, so the class-scoped rows drop out.

    Same rule as a student in no class — the endpoint asks what *this
    person* should see, not what their role should see.
    """
    teacher = _user(session, "t1", ROLE_TEACHER)
    room = _make_class(client, _as(session, teacher))
    _publish(session, [1], title="全班可见", class_id=None)
    _publish(session, [2], title="本班作业", class_id=room["id"])

    body = client.get("/api/v1/my/assignments", headers=_as(session, teacher)).json()
    assert [a["title"] for a in body] == ["全班可见"]
