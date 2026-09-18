"""Tests for the teacher console's aggregations and its guard (阶段 3).

Two things are under test, and the second is the one that matters more:

* the numbers — a teacher is going to make a decision from them, so a
  wrong ordering is a wrong decision, not just a wrong display;
* the **guard** — the console is served from the same bundle students
  use, so the only boundary between them is the server's. A console
  that a student could reach by editing a URL is not a teacher console.

The guard tests are written against the HTTP route rather than against
the dependency, because "the dependency is correct" is not the same as
"every route in this module depends on it" — one route that forgets it
is the whole failure.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_user, issue_session
from app.main import app
from app.models.base import Base
from app.models.class_stats import (
    compute_class_overview,
    compute_error_distribution,
    compute_student_summaries,
    list_flagged_submissions,
)
from app.models.knowledge_stats import compute_knowledge_stats
from app.models.problem import Problem
from app.models.submission import Submission
from app.models.user import ROLE_STUDENT, ROLE_TEACHER
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
    """A client whose auth and teacher routes use the test engine."""
    from app.api import auth as auth_api
    from app.api import teacher as teacher_api

    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)
    monkeypatch.setattr(teacher_api, "get_session", _test_session)

    with TestClient(app) as c:
        yield c


def _problem(session: Session, problem_id: int, tags: list[str]) -> Problem:
    problem = Problem(
        id=problem_id,
        title=f"P{problem_id}",
        knowledge_tags=tags,
        description="",
        samples=[],
    )
    session.add(problem)
    session.commit()
    return problem


def _record(
    session: Session,
    *,
    user_id: int | None,
    problem_id: int,
    status: JudgeStatus,
    flagged: bool = False,
    code: str = "int main(){}",
) -> int:
    """Record one submission directly, bypassing the judge.

    These tests are about aggregation, and the verdict is the only part
    of a judge run the aggregations read.

    Two side effects of the real write path come along, and both matter
    to anything that counts mistake-book rows:

    * a failing verdict **files its own entry** (see
      :func:`app.records._should_archive`), left deliberately unlabelled
      until an analysis lands;
    * two records of *the same code* on the same problem share one entry,
      because archiving deduplicates on a code fingerprint. Pass distinct
      ``code`` when the test needs one entry per submission.
    """
    from app.process_trace import ProcessTrace, describe, from_trace

    submission_id = record_submission(
        JudgeResult(status=status),
        SubmissionContext(
            code=code, language="cpp", problem_id=problem_id, user_id=user_id
        ),
        session=session,
    )
    assert submission_id is not None

    if flagged:
        trace = ProcessTrace(
            events=[{"t": 5_000, "kind": "paste", "chars": 1200, "length": 1200}]
        )
        row = session.get(Submission, submission_id)
        assert row is not None
        features = from_trace(trace, code)
        row.apply_process(features, describe(features).flagged)
        session.commit()
    return submission_id


def _label_the_archived_entry(session: Session, submission_id: int, label: str) -> None:
    """Write the label an analysis would have written.

    The entry already exists — recording a wrong answer created it — so
    the test annotates rather than adds. Adding a second one is how an
    earlier draft of these tests doubled every count.
    """
    from app.models.mistake_book import MistakeBookEntry

    entry = session.scalar(
        select(MistakeBookEntry).where(
            MistakeBookEntry.submission_id == submission_id
        )
    )
    assert entry is not None, "no entry was archived for this submission"
    entry.agent_error_type = label
    session.commit()


# --- The guard ----------------------------------------------------------


def test_the_console_refuses_a_logged_out_request(client: TestClient) -> None:
    assert client.get("/api/v1/teacher/overview").status_code == 401


def test_the_console_refuses_a_student(client: TestClient, session: Session) -> None:
    """403, not 401 — a student is authenticated, just not allowed.

    Conflating the two sends a student's client back to the login screen
    in a loop it cannot escape.
    """
    student = create_user(session, username="stu", password="a")
    token = issue_session(session, student)

    resp = client.get(
        "/api/v1/teacher/overview", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


def test_every_console_route_is_guarded(
    client: TestClient, session: Session
) -> None:
    """One route that forgets the dependency is the whole failure.

    Enumerated rather than sampled: the module grows by copy-paste, and
    a new route that inherits everything except the guard is exactly the
    kind of mistake that survives review.
    """
    student = create_user(session, username="stu", password="a")
    token = issue_session(session, student)
    headers = {"Authorization": f"Bearer {token}"}

    routes = [
        ("GET", "/api/v1/teacher/overview"),
        ("GET", "/api/v1/teacher/knowledge-stats"),
        ("GET", "/api/v1/teacher/error-distribution"),
        ("GET", "/api/v1/teacher/students"),
        ("GET", "/api/v1/teacher/students/1/timeline"),
        ("GET", "/api/v1/teacher/submissions/flagged"),
        ("GET", "/api/v1/teacher/alerts"),
    ]
    for method, path in routes:
        assert client.request(method, path, headers=headers).status_code == 403, path

    # And the write routes, separately: a 405 from a GET — or a 422 from
    # a missing body — would mask a route with no guard at all.
    assert (
        client.post(
            "/api/v1/teacher/students",
            json={"username": "x", "password": "y"},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/teacher/submissions/1/review",
            json={"verdict": "dismissed"},
            headers=headers,
        ).status_code
        == 403
    )


def test_a_teacher_gets_through(client: TestClient, session: Session) -> None:
    teacher = create_user(session, username="t", password="a", role=ROLE_TEACHER)
    token = issue_session(session, teacher)

    resp = client.get(
        "/api/v1/teacher/overview", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


# --- The numbers --------------------------------------------------------


def test_overview_counts_the_class(session: Session) -> None:
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a", display_name="Alice")

    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)
    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.WA)

    overview = compute_class_overview(session)
    assert overview.students == 1
    assert overview.submissions == 2
    assert overview.accepted == 1
    assert overview.accuracy == pytest.approx(0.5)


def test_an_empty_class_is_a_valid_page(session: Session) -> None:
    """Zero rather than a crash, and zero rather than null.

    A teacher opens the console on day one, before anybody has
    submitted anything. A division by zero there is the worst possible
    first impression of a tool.
    """
    overview = compute_class_overview(session)
    assert overview.students == 0
    assert overview.submissions == 0
    assert overview.accuracy == 0.0


def test_error_distribution_is_ranked_most_common_first(session: Session) -> None:
    """The ranking *is* the answer to "what should I teach next".

    Sorted alphabetically, the teacher would have to find the largest
    themselves — and the whole point of the view is that they should
    not have to.
    """
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a")
    for index, label in enumerate(("boundary", "boundary", "off_by_one")):
        # A distinct body per submission: two records of the same code
        # share one archived entry, and this test needs three.
        submission_id = _record(
            session,
            user_id=alice.id,
            problem_id=1,
            status=JudgeStatus.WA,
            code=f"int main(){{}} /* {index} */",
        )
        _label_the_archived_entry(session, submission_id, label)

    rows = compute_error_distribution(session)
    assert [r.label for r in rows] == ["boundary", "off_by_one"]
    assert rows[0].count == 2
    # Absolute tolerance, not the default relative one: ``share`` is
    # rounded to four places on purpose, so 2/3 arrives as 0.6667 and a
    # 1e-6 relative tolerance tests a precision the value never claimed.
    assert rows[0].share == pytest.approx(2 / 3, abs=1e-4)


def test_an_unanalysed_mistake_is_visible_not_dropped(session: Session) -> None:
    """Entries with no label still show up, as one group.

    An analysis outage would otherwise look like a quiet week — the
    teacher sees fewer mistakes rather than fewer *diagnosed* mistakes,
    which is a much more comfortable reading of an outage than the
    truth.

    No entry is added by hand here: recording a wrong answer already
    filed one, and an entry that exists but carries no label *is* the
    case under test.
    """
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a")
    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.WA)

    rows = compute_error_distribution(session)
    assert len(rows) == 1
    assert rows[0].label is None
    assert rows[0].count == 1


def test_students_are_sorted_worst_first(session: Session) -> None:
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a", display_name="Alice")
    bob = create_user(session, username="bob", password="a", display_name="Bob")

    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)
    _record(session, user_id=bob.id, problem_id=1, status=JudgeStatus.WA)

    rows = compute_student_summaries(session)
    assert [r.display_name for r in rows] == ["Bob", "Alice"]
    assert rows[0].accuracy == 0.0
    assert rows[1].accuracy == 1.0


def test_a_student_who_has_not_started_is_still_listed(session: Session) -> None:
    """An absence is the thing a teacher most needs to notice.

    A row that vanished because there was nothing to count would hide
    exactly the student who has not begun.
    """
    create_user(session, username="alice", password="a", display_name="Alice")

    rows = compute_student_summaries(session)
    assert len(rows) == 1
    assert rows[0].submissions == 0
    assert rows[0].accuracy == 0.0


def test_weakest_tag_counts_failures_not_ratios(session: Session) -> None:
    """Two failures out of two outranks four out of ten.

    A percentage hides how long someone has been stuck: 0% on two
    attempts and 40% on ten are not the same student, and the ratio
    alone would rank the second as worse.
    """
    _problem(session, 1, ["动态规划"])
    _problem(session, 2, ["排序"])
    alice = create_user(session, username="alice", password="a")

    # Two attempts on 动态规划, both failed.
    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.WA)
    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.WA)
    # One failed attempt on 排序.
    _record(session, user_id=alice.id, problem_id=2, status=JudgeStatus.WA)

    rows = compute_student_summaries(session)
    assert rows[0].weakest_tag == "动态规划"


def test_knowledge_stats_slice_by_student(session: Session) -> None:
    """The same aggregator, a different slice — not a second one.

    If a class-wide number and a per-student number came from different
    code, they could disagree, and a teacher who noticed would stop
    trusting either.
    """
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a")
    bob = create_user(session, username="bob", password="a")

    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)
    _record(session, user_id=bob.id, problem_id=1, status=JudgeStatus.WA)

    alice_stats = compute_knowledge_stats(session, user_id=alice.id)
    bob_stats = compute_knowledge_stats(session, user_id=bob.id)
    all_stats = compute_knowledge_stats(session)

    assert alice_stats[0].accuracy == 1.0
    assert bob_stats[0].accuracy == 0.0
    assert all_stats[0].total == 2
    assert all_stats[0].accuracy == pytest.approx(0.5)


def test_an_unowned_submission_is_outside_every_student(session: Session) -> None:
    """Unowned is not "everyone's" and not one synthetic student's.

    A teacher looking at 张三's history must not see a stranger's
    attempts mixed in, and the class total is a different question from
    the sum of the students.
    """
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a")

    _record(session, user_id=None, problem_id=1, status=JudgeStatus.AC)
    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.WA)

    assert compute_knowledge_stats(session, user_id=alice.id)[0].total == 1
    assert compute_knowledge_stats(session)[0].total == 2


def test_flagged_submissions_carry_their_evidence(session: Session) -> None:
    """The teacher can check the mark, not just see it.

    A flag with no evidence behind it is a mark a teacher will either
    rubber-stamp or ignore.
    """
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a", display_name="Alice")

    _record(session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC)
    _record(
        session, user_id=alice.id, problem_id=1, status=JudgeStatus.AC, flagged=True
    )

    rows = list_flagged_submissions(session)
    assert len(rows) == 1
    assert rows[0]["student"] == "Alice"
    assert rows[0]["process"]["flagged"] is True
    assert rows[0]["process"]["reasons"]
    assert rows[0]["process"]["features"]["pasted_chars"] == 1200


def test_flagged_count_reaches_the_overview(session: Session) -> None:
    _problem(session, 1, ["排序"])
    alice = create_user(session, username="alice", password="a")
    _record(
        session, user_id=alice.id, problem_id=1, status=JudgeStatus.WA, flagged=True
    )

    assert compute_class_overview(session).flagged_submissions == 1
    assert compute_student_summaries(session)[0].flagged_submissions == 1


# --- Creating accounts --------------------------------------------------


def test_a_teacher_can_add_a_student(client: TestClient, session: Session) -> None:
    teacher = create_user(session, username="t", password="a", role=ROLE_TEACHER)
    token = issue_session(session, teacher)

    resp = client.post(
        "/api/v1/teacher/students",
        json={"username": "stu", "password": "pw", "display_name": "张三"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["display_name"] == "张三"
    assert resp.json()["role"] == ROLE_STUDENT


def test_a_duplicate_roster_entry_is_a_conflict(
    client: TestClient, session: Session
) -> None:
    """409, because the client can act on it.

    A 400 would lump "pick another username" in with "your JSON is
    broken", and only one of those is worth retrying.
    """
    teacher = create_user(session, username="t", password="a", role=ROLE_TEACHER)
    token = issue_session(session, teacher)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
        "/api/v1/teacher/students",
        json={"username": "stu", "password": "pw"},
        headers=headers,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/v1/teacher/students",
        json={"username": "stu", "password": "pw"},
        headers=headers,
    )
    assert second.status_code == 409


def test_a_new_student_can_log_in(client: TestClient, session: Session) -> None:
    """The account a teacher creates actually works.

    The two halves are written by different people — the roster by the
    teacher, the login by the student — and nothing else in the suite
    joins them.
    """
    teacher = create_user(session, username="t", password="a", role=ROLE_TEACHER)
    admin = {"Authorization": f"Bearer {issue_session(session, teacher)}"}
    client.post(
        "/api/v1/teacher/students",
        json={"username": "stu", "password": "pw"},
        headers=admin,
    )

    resp = client.post(
        "/api/v1/auth/login", json={"username": "stu", "password": "pw"}
    )
    assert resp.status_code == 200
