"""Tests for a teacher's verdict on a process flag (阶段 5).

These tests exist for one reason: **a mark nobody can disagree with is a
verdict, not a hint.** The README promises 过程证据提示 + 教师复核, and
the review is the half that makes the promise true. Everything else here
— the columns, the route, the queue predicate — is plumbing around that.

The two properties worth defending explicitly:

* **Dismissing removes the row from the queue without erasing it.** If
  dismissing deleted the numbers, the question "how often is the rule
  wrong?" would become unanswerable, and that question is the only way
  the thresholds in :mod:`app.process_trace` ever get tuned on evidence.
* **Unflagged rows never enter the queue.** The queue predicate is a
  conjunction, and a conjunction that loses a half is the classic way a
  "review these" list turns into "every submission in the database".
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
    compute_student_summaries,
    list_flagged_submissions,
)
from app.models.problem import Problem
from app.models.submission import (
    REVIEW_CLEARED,
    REVIEW_CONFIRMED,
    REVIEW_DISMISSED,
    Submission,
)
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
    """A client whose teacher routes use the test engine."""
    from app.api import auth as auth_api
    from app.api import teacher as teacher_api

    SessionLocal = sessionmaker(engine, expire_on_commit=False)

    def _test_session() -> Session:
        return SessionLocal()

    monkeypatch.setattr(auth_api, "get_session", _test_session)
    monkeypatch.setattr(teacher_api, "get_session", _test_session)

    with TestClient(app) as c:
        yield c


def _flagged(session: Session, code: str = "int main(){}") -> int:
    """One flagged submission, without going through the judge.

    The flag is written the way the write path writes it — derived from a
    trace — rather than by setting the boolean, because a test that sets
    the conclusion directly would pass even if the derivation stopped
    being able to produce it.
    """
    from app.process_trace import ProcessTrace, describe, from_trace

    problem = session.get(Problem, 1)
    if problem is None:
        session.add(
            Problem(
                id=1, title="P1", knowledge_tags=["排序"], description="", samples=[]
            )
        )
        session.commit()

    submission_id = record_submission(
        JudgeResult(status=JudgeStatus.WA),
        SubmissionContext(code=code, language="cpp", problem_id=1),
        session=session,
    )
    assert submission_id is not None

    row = session.get(Submission, submission_id)
    assert row is not None
    trace = ProcessTrace(
        events=[{"t": 5_000, "kind": "paste", "chars": 1200, "length": 1200}]
    )
    features = from_trace(trace, code)
    row.apply_process(features, describe(features).flagged)
    session.commit()
    assert row.process_flagged is True
    return submission_id


def _row(session: Session, submission_id: int) -> Submission:
    row = session.get(Submission, submission_id)
    assert row is not None
    return row


# --- The verdict itself -------------------------------------------------


def test_a_fresh_flag_is_open(session: Session) -> None:
    submission_id = _flagged(session)
    row = _row(session, submission_id)
    assert row.process_review is None
    assert row.is_open_flag is True


def test_dismissing_closes_the_flag(session: Session) -> None:
    row = _row(session, _flagged(session))
    row.apply_review(REVIEW_DISMISSED, reviewer_id=7)
    assert row.process_review == REVIEW_DISMISSED
    assert row.is_open_flag is False


def test_confirming_leaves_the_flag_in_the_list(session: Session) -> None:
    """Confirmed is not the same as dealt-with-and-forget.

    A confirmed flag is a real case the teacher may still want in front
    of them; a dismissed one is noise they have asked not to see again.
    Collapsing the two into "reviewed" would make the list empty itself
    under the teacher as they work through it.
    """
    row = _row(session, _flagged(session))
    row.apply_review(REVIEW_CONFIRMED, reviewer_id=7)
    assert row.is_open_flag is True


def test_clearing_reopens_the_flag(session: Session) -> None:
    """A dismissal taken back puts the row back in the queue.

    The recovery path for a misclick, which otherwise leaves a real flag
    hidden with no way to undo it and no trace of why.
    """
    row = _row(session, _flagged(session))
    row.apply_review(REVIEW_DISMISSED, reviewer_id=7)
    row.apply_review(REVIEW_CLEARED, reviewer_id=7)
    assert row.process_review is None
    assert row.process_review_by is None
    assert row.process_review_at is None
    assert row.is_open_flag is True


def test_an_unknown_verdict_is_refused(session: Session) -> None:
    row = _row(session, _flagged(session))
    with pytest.raises(ValueError):
        row.apply_review("probably", reviewer_id=7)
    assert row.process_review is None


def test_the_reviewer_and_the_time_are_kept(session: Session) -> None:
    row = _row(session, _flagged(session))
    row.apply_review(REVIEW_CONFIRMED, reviewer_id=11)
    session.commit()

    reloaded = _row(session, row.id)
    assert reloaded.process_review_by == 11
    assert reloaded.process_review_at is not None


def test_dismissing_keeps_the_evidence(session: Session) -> None:
    """The one thing this whole design is protecting.

    A dismissal is an opinion about the measurement, not a replacement
    for it. Overwriting the flag with the verdict is the obvious schema
    and it destroys the only data that could ever tell us whether the
    rule over-fires.
    """
    row = _row(session, _flagged(session))
    pasted = row.process_pasted_chars
    row.apply_review(REVIEW_DISMISSED, reviewer_id=7)
    session.commit()

    reloaded = _row(session, row.id)
    assert reloaded.process_flagged is True
    assert reloaded.process_pasted_chars == pasted
    assert reloaded.process_review == REVIEW_DISMISSED


# --- The queue ----------------------------------------------------------


def test_a_dismissed_flag_leaves_the_queue(session: Session) -> None:
    submission_id = _flagged(session)
    assert [r["id"] for r in list_flagged_submissions(session)] == [submission_id]

    _row(session, submission_id).apply_review(REVIEW_DISMISSED, reviewer_id=7)
    session.commit()
    assert list_flagged_submissions(session) == []


def test_unflagged_rows_never_enter_the_queue(session: Session) -> None:
    """The conjunction's other half.

    The queue predicate is "flagged AND not dismissed", and it is
    tempting to write only the second half because it is the newer one.
    Every unflagged row satisfies "not dismissed", so dropping the first
    half would list the entire submission history as suspicious.
    """
    session.add(
        Problem(id=1, title="P1", knowledge_tags=["排序"], description="", samples=[])
    )
    session.commit()
    submission_id = record_submission(
        JudgeResult(status=JudgeStatus.AC),
        SubmissionContext(code="int main(){}", language="cpp", problem_id=1),
        session=session,
    )
    assert submission_id is not None
    assert list_flagged_submissions(session) == []


def test_include_reviewed_brings_dismissed_rows_back(session: Session) -> None:
    """What the teacher decided last week is still a real question."""
    submission_id = _flagged(session)
    _row(session, submission_id).apply_review(REVIEW_DISMISSED, reviewer_id=7)
    session.commit()

    assert list_flagged_submissions(session) == []
    rows = list_flagged_submissions(session, include_reviewed=True)
    assert [r["id"] for r in rows] == [submission_id]
    assert rows[0]["review"]["verdict"] == REVIEW_DISMISSED


def test_the_queue_row_carries_the_review_state(session: Session) -> None:
    """So the client can render "已驳回" without a second request."""
    _flagged(session)
    rows = list_flagged_submissions(session)
    assert rows[0]["review"] == {
        "verdict": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }


def test_the_overview_reports_both_numbers(session: Session) -> None:
    """Counted separately because they answer different questions.

    ``flagged_submissions`` is how trigger-happy the detector is;
    ``open_flags`` is how much reading there is to do. Reporting only the
    second would let a flag that fires on everything look like it fires
    on nothing, as soon as somebody disciplined dismissed them all.
    """
    first = _flagged(session, code="int main(){int a=1;}")
    second = _flagged(session, code="int main(){int b=2;}")

    overview = compute_class_overview(session)
    assert overview.flagged_submissions == 2
    assert overview.open_flags == 2

    _row(session, first).apply_review(REVIEW_DISMISSED, reviewer_id=7)
    _row(session, second).apply_review(REVIEW_CONFIRMED, reviewer_id=7)
    session.commit()

    overview = compute_class_overview(session)
    assert overview.flagged_submissions == 2
    assert overview.open_flags == 1


def test_a_students_flag_count_follows_the_verdict(session: Session) -> None:
    alice = create_user(session, username="alice", password="a", display_name="Alice")

    submission_id = _flagged(session)
    row = _row(session, submission_id)
    row.user_id = alice.id
    session.commit()

    assert compute_student_summaries(session)[0].flagged_submissions == 1

    row.apply_review(REVIEW_DISMISSED, reviewer_id=7)
    session.commit()
    assert compute_student_summaries(session)[0].flagged_submissions == 0


# --- The route ----------------------------------------------------------


def _teacher_token(session: Session) -> str:
    teacher = create_user(session, username="t", password="a", role=ROLE_TEACHER)
    return issue_session(session, teacher)


def test_a_teacher_can_dismiss_a_flag(client: TestClient, session: Session) -> None:
    submission_id = _flagged(session)
    headers = {"Authorization": f"Bearer {_teacher_token(session)}"}

    resp = client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "dismissed"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["review"]["verdict"] == "dismissed"

    rows = list_flagged_submissions(session)
    assert rows == []


def test_the_review_is_attributed_to_the_teacher_who_made_it(
    client: TestClient, session: Session
) -> None:
    submission_id = _flagged(session)
    teacher = create_user(session, username="t2", password="a", role=ROLE_TEACHER)
    headers = {"Authorization": f"Bearer {issue_session(session, teacher)}"}

    client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "confirmed"},
        headers=headers,
    )
    assert _row(session, submission_id).process_review_by == teacher.id


def test_reviewing_an_unflagged_submission_is_refused(
    client: TestClient, session: Session
) -> None:
    """409, not a silent success.

    Storing a verdict on a row that was never flagged would invent a
    disagreement that never happened and corrupt the one count that
    measures how often the flag over-fires.
    """
    session.add(
        Problem(id=1, title="P1", knowledge_tags=["排序"], description="", samples=[])
    )
    session.commit()
    submission_id = record_submission(
        JudgeResult(status=JudgeStatus.AC),
        SubmissionContext(code="int main(){}", language="cpp", problem_id=1),
        session=session,
    )

    resp = client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "confirmed"},
        headers={"Authorization": f"Bearer {_teacher_token(session)}"},
    )
    assert resp.status_code == 409


def test_reviewing_a_missing_submission_is_a_404(
    client: TestClient, session: Session
) -> None:
    resp = client.post(
        "/api/v1/teacher/submissions/9999/review",
        json={"verdict": "confirmed"},
        headers={"Authorization": f"Bearer {_teacher_token(session)}"},
    )
    assert resp.status_code == 404


def test_an_unknown_verdict_is_rejected_by_the_api(
    client: TestClient, session: Session
) -> None:
    submission_id = _flagged(session)
    resp = client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "looks-suspicious"},
        headers={"Authorization": f"Bearer {_teacher_token(session)}"},
    )
    assert resp.status_code == 422


def test_the_review_route_is_teacher_only(client: TestClient, session: Session) -> None:
    """The review can rewrite what a teacher sees; it is not student-facing."""
    submission_id = _flagged(session)

    student = create_user(session, username="stu", password="a")
    student_headers = {"Authorization": f"Bearer {issue_session(session, student)}"}
    resp = client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "dismissed"},
        headers=student_headers,
    )
    assert resp.status_code == 403
    assert _row(session, submission_id).process_review is None

    anon = client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "dismissed"},
    )
    assert anon.status_code in (401, 403)


def test_the_row_is_still_there_after_a_dismissal(
    client: TestClient, session: Session
) -> None:
    """Dismissed is not deleted — visible through the explicit flag."""
    submission_id = _flagged(session)
    headers = {"Authorization": f"Bearer {_teacher_token(session)}"}
    client.post(
        f"/api/v1/teacher/submissions/{submission_id}/review",
        json={"verdict": "dismissed"},
        headers=headers,
    )

    resp = client.get(
        "/api/v1/teacher/submissions/flagged?include_reviewed=true", headers=headers
    )
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()["items"]] == [submission_id]


def test_the_stored_rows_survive_a_round_trip(session: Session) -> None:
    """Guards the column definitions against the model, not just the code."""
    submission_id = _flagged(session)
    _row(session, submission_id).apply_review(REVIEW_CONFIRMED, reviewer_id=3)
    session.commit()

    row = session.scalar(select(Submission).where(Submission.id == submission_id))
    assert row is not None
    assert row.process_review == REVIEW_CONFIRMED
    assert row.process_review_by == 3
