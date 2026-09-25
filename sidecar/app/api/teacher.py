"""Teacher console endpoints (阶段 3).

GET  /api/v1/teacher/overview
GET  /api/v1/teacher/knowledge-stats?user_id=
GET  /api/v1/teacher/error-distribution?user_id=
GET  /api/v1/teacher/students
GET  /api/v1/teacher/students/{id}/timeline
GET  /api/v1/teacher/submissions/flagged
POST /api/v1/teacher/submissions/{id}/review
GET  /api/v1/teacher/alerts
POST /api/v1/teacher/students

Who is allowed here
-------------------
Every route in this module is teacher-only. That is enforced by a
dependency (:func:`app.api.auth.require_teacher`) rather than by the
client hiding the page: the console is served from the same bundle the
students use, so "the student's build does not link to it" was never a
boundary — the request is.

Why the drill-down takes ``user_id`` and not a role check
---------------------------------------------------------
A teacher looking at one student is still a teacher, so the same guard
applies; the ``user_id`` parameter is a filter, not an authorization
decision. The two get confused easily and the difference matters: if
authorization were by ``user_id``, any student could read anyone's
history by changing a number in a URL.

What is deliberately absent
---------------------------
Grades, attendance, and anything that writes to a student's record
except creating the account itself. The console is a *teaching
decision* tool, and the moment it starts carrying marks it becomes
something a teacher has to defend.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_teacher
from app.auth import create_user
from app.db import get_session
from app.models.alerts import Alert, compute_alerts
from app.models.class_stats import (
    compute_class_overview,
    compute_error_distribution,
    compute_student_summaries,
    list_flagged_submissions,
)
from app.models.knowledge_stats import compute_knowledge_stats
from app.models.mistake_book import MistakeBookEntry
from app.models.submission import Submission
from app.models.user import ROLE_STUDENT, User

router = APIRouter(prefix="/api/v1/teacher", tags=["teacher"])


class ErrorCountOut(BaseModel):
    label: Optional[str] = None
    description: str = ""
    count: int
    share: float


class KnowledgeStatOut(BaseModel):
    tag: str
    total: int
    passed: int
    accuracy: float


class StudentOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    submissions: int
    accepted: int
    accuracy: float
    mistakes: int
    flagged_submissions: int
    weakest_tag: Optional[str] = None


class TimelineEntryOut(BaseModel):
    id: int
    problem_id: Optional[int] = None
    status: Optional[str] = None
    error_type: Optional[str] = None
    error_description: str = ""
    created_at: Optional[str] = None


class AlertOut(BaseModel):
    """One student with a reason.

    No severity number on the wire on purpose — see
    :mod:`app.models.alerts`: a score the teacher cannot interrogate is a
    verdict, and the reason is the part they act on.
    """

    user_id: int
    display_name: str
    kind: str
    reason: str


class CreateStudentRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class ReviewRequest(BaseModel):
    """A teacher's verdict on one flagged submission (阶段 5).

    ``cleared`` is "I take it back" rather than a fourth state: the
    stored column goes back to NULL, which is what makes the queue able
    to re-admit a row somebody dismissed by accident.

    A ``Literal`` rather than a plain ``str`` plus a check in the route:
    an accepted-but-unknown value would otherwise be persisted as a
    status no query matches, and the row would leave the queue forever
    with nothing on screen saying why.
    """

    verdict: Literal["confirmed", "dismissed", "cleared"]


@router.get("/overview")
def overview(_teacher: User = Depends(require_teacher)) -> dict:
    """The numbers at the top of the console."""
    with get_session() as session:
        return compute_class_overview(session).to_dict()


@router.get("/knowledge-stats", response_model=list[KnowledgeStatOut])
def knowledge_stats(
    user_id: Optional[int] = Query(default=None),
    _teacher: User = Depends(require_teacher),
) -> list[KnowledgeStatOut]:
    """Per-tag accuracy, class-wide or for one student.

    The same aggregator the student's own profile page uses, sliced by
    owner — which is exactly what the README means by "复用现有聚合
    SQL，换切片维度". Two implementations of this number would answer
    two different questions.
    """
    with get_session() as session:
        stats = compute_knowledge_stats(session, user_id=user_id)
    return [
        KnowledgeStatOut(
            tag=s.tag, total=s.total, passed=s.passed, accuracy=s.accuracy
        )
        for s in stats
    ]


@router.get("/error-distribution", response_model=list[ErrorCountOut])
def error_distribution(
    user_id: Optional[int] = Query(default=None),
    _teacher: User = Depends(require_teacher),
) -> list[ErrorCountOut]:
    """Mistake counts per root-cause label, most common first.

    This is the "what should I teach next" answer: if the top entry is
    边界条件, that is the lesson. Sorted by count because the ranking
    *is* the content — see :func:`compute_error_distribution`.
    """
    with get_session() as session:
        rows = compute_error_distribution(session, user_id=user_id)
    return [
        ErrorCountOut(
            label=r.label, description=r.description, count=r.count, share=r.share
        )
        for r in rows
    ]


@router.get("/students", response_model=list[StudentOut])
def students(_teacher: User = Depends(require_teacher)) -> list[StudentOut]:
    """Every student, worst first — the answer to "who needs me"."""
    with get_session() as session:
        rows = compute_student_summaries(session)
    return [
        StudentOut(
            user_id=r.user_id,
            username=r.username,
            display_name=r.display_name,
            submissions=r.submissions,
            accepted=r.accepted,
            accuracy=r.accuracy,
            mistakes=r.mistakes,
            flagged_submissions=r.flagged_submissions,
            weakest_tag=r.weakest_tag,
        )
        for r in rows
    ]


@router.get("/students/{user_id}/timeline", response_model=list[TimelineEntryOut])
def student_timeline(
    user_id: int, _teacher: User = Depends(require_teacher)
) -> list[TimelineEntryOut]:
    """One student's mistakes, oldest first.

    The timeline is built from the mistake book, not from submissions:
    what a teacher wants to see is "here is what this student got wrong,
    in the order they got it wrong", and a submission list would pad
    that with every accepted run.
    """
    from sqlalchemy import select as sa_select

    with get_session() as session:
        rows = session.execute(
            sa_select(MistakeBookEntry, Submission)
            .join(Submission, MistakeBookEntry.submission_id == Submission.id)
            .where(Submission.user_id == user_id)
            .order_by(MistakeBookEntry.id)
        ).all()

        from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS

        return [
            TimelineEntryOut(
                id=entry.id,
                problem_id=entry.problem_id,
                status=submission.status,
                error_type=entry.agent_error_type,
                error_description=ROOT_CAUSE_DESCRIPTIONS.get(
                    entry.agent_error_type or "", ""
                ),
                created_at=(
                    entry.created_at.isoformat() if entry.created_at else None
                ),
            )
            for entry, submission in rows
        ]


@router.get("/submissions/flagged")
def flagged_submissions(
    limit: int = Query(default=50, ge=1, le=200),
    include_reviewed: bool = Query(default=False),
    _teacher: User = Depends(require_teacher),
) -> dict:
    """Submissions carrying a process flag (C1).

    Returns the evidence, not just the flag: a mark a teacher cannot
    check is a mark they will either rubber-stamp or ignore, and the
    README promises it is reviewable.

    Reviewed rows are left out unless asked for — see
    :func:`app.models.class_stats.list_flagged_submissions`.
    """
    with get_session() as session:
        return {
            "items": list_flagged_submissions(
                session, limit=limit, include_reviewed=include_reviewed
            )
        }


@router.get("/alerts", response_model=list[AlertOut])
def alerts(_teacher: User = Depends(require_teacher)) -> list[Alert]:
    """Who needs attention, and why (阶段 6).

    Every row carries the numbers that fired its rule, because "需要关注"
    is not something a teacher can act on and "最近 5 次提交都没通过" is.
    """
    with get_session() as session:
        return compute_alerts(session)


@router.post("/submissions/{submission_id}/review")
def review_flag(
    submission_id: int,
    req: ReviewRequest,
    teacher: User = Depends(require_teacher),
) -> dict:
    """Confirm or dismiss the process flag on one submission.

    This is the half of 阶段 5 that makes the signal *reviewable* rather
    than merely visible: without it the flag is a verdict the teacher can
    only rubber-stamp.

    Errors:
        404: No such submission.
        409: The submission carries no flag, so there is nothing to have
            an opinion about. Rejected rather than silently accepted —
            storing a verdict on an unflagged row would invent a
            disagreement that never happened, and it would corrupt the
            one number that could otherwise tell us whether the flag
            over-fires.

    Returns:
        The updated submission, without its source.
    """
    with get_session() as session:
        row = session.get(Submission, submission_id)
        if row is None:
            raise HTTPException(status_code=404, detail="submission not found")
        if not row.process_flagged:
            raise HTTPException(
                status_code=409, detail="this submission carries no process flag"
            )
        row.apply_review(req.verdict, reviewer_id=teacher.id)
        session.commit()
        return row.to_dict(include_source=False)


@router.post("/students", status_code=201)
def add_student(
    req: CreateStudentRequest, _teacher: User = Depends(require_teacher)
) -> dict:
    """Create a student account.

    Students *can* now register themselves
    (``POST /api/v1/auth/register``, added 2026-09-25), so this endpoint
    is no longer the only way an account comes into existence. It stays
    because a teacher building a roster in one go is still the normal
    path for a class, and because it can do two things self-registration
    deliberately cannot: set the display name to whatever the teacher
    wants, and create the account *before* the student ever shows up.

    Returns 409 for a taken username rather than 400: the client can do
    something about it (pick another name), and a 400 would lump it in
    with a malformed body.
    """
    try:
        with get_session() as session:
            user = create_user(
                session,
                username=req.username,
                password=req.password,
                role=ROLE_STUDENT,
                display_name=req.display_name,
            )
            return user.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


class ImportStudentsRequest(BaseModel):
    """A pasted roster (阶段 5).

    ``csv`` is the raw text, not a parsed list. Letting the client parse
    would put the column order, the header handling and the quoting
    rules in the browser — where the same file would then import
    differently from a different client. Parsing lives here so there is
    one answer to "what does this file mean".
    """

    csv: str = Field(min_length=1, max_length=200_000)
    #: Applied to every created account. A default rather than a column:
    #: a class roster imported in one go shares one, and requiring it per
    #: row would make the file format carry a value nobody varies.
    default_password: str = Field(default="123456", min_length=1)
    #: Treat the first line as column names. Detected by the caller rather
    #: than guessed here — see `import_students`.
    has_header: bool = True


class ImportedStudentOut(BaseModel):
    username: str
    display_name: str
    ok: bool
    #: Why it failed, in Chinese. `''` when `ok`.
    error: str = ""


class ImportStudentsResult(BaseModel):
    created: int
    skipped: int
    results: list[ImportedStudentOut]


def _parse_roster_csv(csv_text: str, has_header: bool) -> list[tuple[str, str]]:
    """``text`` → ``[(username, display_name), ...]``.

    Accepts two columns (`username,display_name`) or three
    (`username,password,display_name`) — the second is ignored because
    the request carries one password for the batch. That tolerance is
    deliberate: rosters get exported from wherever the school keeps them,
    and refusing a whole file over a column nobody needed would mean
    hand-editing it.

    Blank lines and whitespace-only lines are dropped rather than
    reported: an exported file reliably ends with one, and calling that
    a failure would put a meaningless row in the preview.
    """
    import csv as csv_mod
    import io as io_mod

    reader = csv_mod.reader(io_mod.StringIO(csv_text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return []
    if has_header:
        rows = rows[1:]

    out: list[tuple[str, str]] = []
    for row in rows:
        username = row[0].strip() if len(row) > 0 else ""
        if len(row) >= 3:
            display_name = row[2].strip()
        elif len(row) == 2:
            display_name = row[1].strip()
        else:
            display_name = ""
        out.append((username, display_name))
    return out


@router.post("/students/import", response_model=ImportStudentsResult)
def import_students(
    req: ImportStudentsRequest, _teacher: User = Depends(require_teacher)
) -> ImportStudentsResult:
    """Add a whole roster at once (阶段 5).

    **Per-row results, not all-or-nothing.** A roster of forty with one
    taken username must still create the other thirty-nine — a transaction
    that rolls back the batch would make the teacher find and fix the
    conflict before anything happens, and the failure they can act on is
    one name, not the file.

    Duplicates inside the file itself are reported rather than silently
    collapsed: "ok" for a row that was dropped as a repeat of the row
    above it would be a lie about what is in the database.
    """
    pairs = _parse_roster_csv(req.csv, req.has_header)
    if not pairs:
        raise HTTPException(status_code=422, detail="没有解析出任何一行")

    results: list[ImportedStudentOut] = []
    seen: set[str] = set()
    with get_session() as session:
        for username, display_name in pairs:
            if not username:
                results.append(
                    ImportedStudentOut(
                        username="",
                        display_name=display_name,
                        ok=False,
                        error="用户名为空",
                    )
                )
                continue
            if username in seen:
                results.append(
                    ImportedStudentOut(
                        username=username,
                        display_name=display_name,
                        ok=False,
                        error="文件里重复出现",
                    )
                )
                continue
            seen.add(username)
            try:
                user = create_user(
                    session,
                    username=username,
                    password=req.default_password,
                    role=ROLE_STUDENT,
                    display_name=display_name or username,
                )
                results.append(
                    ImportedStudentOut(
                        username=username,
                        display_name=user.display_name or username,
                        ok=True,
                    )
                )
            except ValueError as exc:
                results.append(
                    ImportedStudentOut(
                        username=username,
                        display_name=display_name,
                        ok=False,
                        error=str(exc),
                    )
                )

    created = sum(1 for r in results if r.ok)
    return ImportStudentsResult(
        created=created, skipped=len(results) - created, results=results
    )
