"""Homework endpoints (阶段 4).

POST   /api/v1/teacher/assignments
GET    /api/v1/teacher/assignments
GET    /api/v1/teacher/assignments/{id}/stats
DELETE /api/v1/teacher/assignments/{id}
GET    /api/v1/assignments

Two of these are teacher-only and one is deliberately open, and the
split is the whole design: **publishing homework is a teacher action,
reading it is not.** A student who cannot see the list cannot do the
work, and gating the read behind a role would mean either logging every
student in or maintaining a second copy of the list.

What is not here
----------------
Per-student status, marks, late flags, resubmission rules. See
:mod:`app.models.assignment`: this is a set of problems with a date, and
everything else is derived at read time. A "closed" flag is also absent
— a past due date is a fact about the calendar, and a flag would be a
fact somebody has to remember to set.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_teacher, require_user
from app.assistance import (
    DEFAULT_ASSISTANCE_LEVEL,
    MAX_ASSISTANCE_LEVEL,
    MIN_ASSISTANCE_LEVEL,
)
from app.db import get_session
from app.models.assignment import Assignment
from app.models.assignment_stats import compute_assignment_stats
from app.models.classroom import ClassMember, Classroom
from app.models.problem import Problem
from app.models.user import User

router = APIRouter(prefix="/api/v1", tags=["assignments"])


class CreateAssignmentRequest(BaseModel):
    """What the teacher types.

    ``problem_ids`` is validated against the catalogue rather than
    trusted: a typo would otherwise publish an assignment pointing at a
    problem that does not exist, and it would fail as an empty stats
    screen with nothing anywhere saying why.

    ``assistance_level`` is the teacher's decision about how much AI help
    the class may use (1–5). Validated here rather than normalized
    silently: a teacher who sends 7 made a mistake, and quietly turning
    it into 3 would publish homework with a rule nobody chose. Reads are
    lenient (see :func:`app.assistance.normalize_level`) because a bad
    row must not break a page; **writes are strict** because a bad
    request is a person, and they can be told.
    """

    title: str = Field(min_length=1, max_length=128)
    description: str = ""
    problem_ids: list[int] = Field(min_length=1)
    due_at: Optional[str] = None
    assistance_level: int = Field(
        default=DEFAULT_ASSISTANCE_LEVEL,
        ge=MIN_ASSISTANCE_LEVEL,
        le=MAX_ASSISTANCE_LEVEL,
    )
    #: Which class to set it for, or ``None`` for "anyone". Optional so
    #: that the practice-problem case needs no class to exist, and so
    #: every client written before classes did keeps working unchanged.
    class_id: Optional[int] = None


class AssignmentOut(BaseModel):
    id: int
    title: str
    description: str
    problem_ids: list[int]
    due_at: Optional[str] = None
    overdue: bool = False
    #: 1–5. Kept alongside the expanded policy so a client can show the
    #: number without the object, and vice versa.
    assistance_level: int = DEFAULT_ASSISTANCE_LEVEL
    #: The rules for that level, expanded. Sent rather than left for the
    #: client to look up so there is exactly one definition of what each
    #: level allows (app/assistance.py) and both ends read the same one.
    assistance: dict = Field(default_factory=dict)
    created_by: Optional[int] = None
    created_at: Optional[str] = None


class ProblemStatOut(BaseModel):
    problem_id: int
    submissions: int
    accepted: int
    accuracy: float
    students: int


class AssignmentStatsOut(BaseModel):
    assignment: AssignmentOut
    problems: list[ProblemStatOut]
    students_who_submitted: int
    students_missing: list[str]


def _parse_due_at(raw: Optional[str]):
    """Parse an ISO date, rejecting anything else.

    A due date that silently failed to parse and landed as ``None``
    would look exactly like "no deadline", which is the opposite of what
    was asked for.
    """
    if raw is None or raw == "":
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"due_at is not an ISO datetime: {raw}"
        ) from exc


@router.post("/teacher/assignments", status_code=201, response_model=AssignmentOut)
def create_assignment(
    req: CreateAssignmentRequest, teacher: User = Depends(require_teacher)
) -> AssignmentOut:
    """Publish homework.

    Returns 404 for an unknown problem id rather than 422: the body is
    well-formed, it just names something that does not exist, and the
    client can act on that by picking a different problem.

    ``class_id`` is checked against ownership, not merely existence. A
    teacher publishing into another teacher's class would otherwise put
    work in front of students they do not teach, and the console they
    cannot open would be the only place it showed up.
    """
    with get_session() as session:
        if req.class_id is not None:
            room = session.get(Classroom, req.class_id)
            if room is None or room.teacher_id != teacher.id:
                raise HTTPException(
                    status_code=404, detail=f"no classroom with id {req.class_id}"
                )
        for problem_id in req.problem_ids:
            if session.get(Problem, problem_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"no problem with id {problem_id}"
                )
        row = Assignment(
            title=req.title,
            description=req.description,
            problem_ids=list(req.problem_ids),
            due_at=_parse_due_at(req.due_at),
            assistance_level=req.assistance_level,
            created_by=teacher.id,
            class_id=req.class_id,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return AssignmentOut(**row.to_dict())


@router.get("/teacher/assignments", response_model=list[AssignmentOut])
def list_assignments(_teacher: User = Depends(require_teacher)) -> list[AssignmentOut]:
    """Every assignment, newest first."""
    from sqlalchemy import select

    with get_session() as session:
        rows = (
            session.execute(select(Assignment).order_by(Assignment.id.desc()))
            .scalars()
            .all()
        )
        return [AssignmentOut(**row.to_dict()) for row in rows]


class DuplicateAssignmentRequest(BaseModel):
    """Renaming and re-dating are the two things a teacher always changes.

    Both optional: re-publishing the same homework to a new class is the
    other half of the use, and it wants the original title and no
    deadline."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=128)
    due_at: Optional[str] = None


@router.post(
    "/teacher/assignments/{assignment_id}/duplicate",
    status_code=201,
    response_model=AssignmentOut,
)
def duplicate_assignment(
    assignment_id: int,
    req: DuplicateAssignmentRequest,
    teacher: User = Depends(require_teacher),
) -> AssignmentOut:
    """Copy one assignment into a new one (阶段 5).

    Homework is reused term after term with the same problem set, so the
    costly part is re-typing the ids — and re-typing is where a wrong
    problem silently gets included. This copies the set verbatim.

    **The assistance level is copied too**, or rather: it is taken from
    the source unless the request overrides it. The alternative — falling
    back to the default — would mean a duplicate of a 一档 assignment
    quietly allows AI chat, which is the one direction that matters.

    Nothing about submissions is copied. The new row has no history by
    construction, which is the point: "who has done it" restarts.
    """

    with get_session() as session:
        source = session.get(Assignment, assignment_id)
        if source is None:
            raise HTTPException(
                status_code=404, detail=f"no assignment with id {assignment_id}"
            )
        # The class is carried over only if this teacher still owns it.
        # A duplicate of an assignment that was set for someone else's
        # class must not quietly publish into that class — the same
        # ownership check `create_assignment` makes, for the same reason.
        class_id = source.class_id
        if class_id is not None:
            room = session.get(Classroom, class_id)
            if room is None or room.teacher_id != teacher.id:
                class_id = None

        row = Assignment(
            title=req.title if req.title is not None else f"{source.title}（副本）",
            description=source.description,
            problem_ids=list(source.problem_ids or []),
            due_at=_parse_due_at(req.due_at) if req.due_at is not None else None,
            assistance_level=source.assistance_level,
            created_by=teacher.id,
            class_id=class_id,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return AssignmentOut(**row.to_dict())


@router.get(
    "/teacher/assignments/{assignment_id}/stats", response_model=AssignmentStatsOut
)
def assignment_stats(
    assignment_id: int, _teacher: User = Depends(require_teacher)
) -> AssignmentStatsOut:
    """How the class is doing on one assignment.

    Carries the list of students who have *not* submitted, which is the
    only part of this screen anyone acts on — see
    :mod:`app.models.assignment_stats`.
    """
    try:
        with get_session() as session:
            stats = compute_assignment_stats(session, assignment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return AssignmentStatsOut(
        assignment=AssignmentOut(**stats.assignment),
        problems=[
            ProblemStatOut(
                problem_id=p.problem_id,
                submissions=p.submissions,
                accepted=p.accepted,
                accuracy=p.accuracy,
                students=p.students,
            )
            for p in stats.problems
        ],
        students_who_submitted=stats.students_who_submitted,
        students_missing=stats.students_missing,
    )


@router.delete("/teacher/assignments/{assignment_id}", status_code=204)
def delete_assignment(
    assignment_id: int, _teacher: User = Depends(require_teacher)
) -> None:
    """Un-publish homework.

    Deletes the assignment only. Submissions are history and stay —
    removing the thing that asked for them must not erase the work.
    """
    with get_session() as session:
        row = session.get(Assignment, assignment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="assignment not found")
        session.delete(row)
        session.commit()


@router.get("/assignments", response_model=list[AssignmentOut])
def open_assignments() -> list[AssignmentOut]:
    """The homework list, for anyone who asks.

    Open by design — a student has to be able to see what was set
    without being logged in, because the no-login path is how the
    desktop client works today (and every submission on it is still
    theirs, just unattributed).
    """
    from sqlalchemy import select

    with get_session() as session:
        rows = (
            session.execute(select(Assignment).order_by(Assignment.id.desc()))
            .scalars()
            .all()
        )
        return [AssignmentOut(**row.to_dict()) for row in rows]


class StudentAssignmentRow(BaseModel):
    """One cell of a student's report: how they did on one assignment."""

    assignment_id: int
    title: str
    due_at: Optional[str] = None
    overdue: bool = False
    problem_count: int = 0
    submissions: int = 0
    accepted: int = 0
    #: Accepted problems over the assignment's problems. `None` — not 0.0 —
    #: when nothing has been submitted, because "did none of it" and
    #: "got none of it right" are different facts about a student.
    progress: Optional[float] = None
    last_submitted_at: Optional[str] = None


class StudentReportOut(BaseModel):
    user_id: int
    display_name: str
    username: str
    rows: list[StudentAssignmentRow]
    #: Assignments with at least one submission over assignments set.
    completed: int = 0
    total_assignments: int = 0


@router.get("/teacher/students/{user_id}/report", response_model=StudentReportOut)
def student_report(
    user_id: int, _teacher: User = Depends(require_teacher)
) -> StudentReportOut:
    """One student against every assignment (阶段 5).

    The console's existing timeline answers "what did they get wrong, in
    order" from the mistake book. This answers a different question the
    same teacher asks at report time — "how is this student doing across
    the term" — and it has to be built from assignments, because a
    student who submitted nothing at all has no mistakes and would
    therefore be invisible in any mistake-book view.

    That is the whole reason this endpoint exists: the absent student is
    the one worth noticing, and the timeline can never show them.
    """
    from sqlalchemy import case, func, select

    from app.models.submission import Submission
    from app.models.user import User as UserModel

    with get_session() as session:
        student = session.get(UserModel, user_id)
        if student is None:
            raise HTTPException(status_code=404, detail=f"no user with id {user_id}")

        assignments = (
            session.execute(select(Assignment).order_by(Assignment.id.desc()))
            .scalars()
            .all()
        )

        # One grouped query rather than one per assignment: a term of
        # homework is a few dozen rows and this keeps it to two round
        # trips regardless.
        # `dict()` cannot take 3-tuples — it is the two-column case only.
        # Keyed by problem_id, valued by (submissions, accepted).
        #
        # The literal is `"AC"`, the stored value — not `"Accepted"`, which
        # is what the *response* spells it as (see `VerdictBadge`). The two
        # differ, and a filter on the wrong one matches nothing: the count
        # comes back 0 rather than erroring, so every student reads as
        # having passed nothing.
        stats: dict[int, tuple[int, int]] = {
            row[0]: (int(row[1] or 0), int(row[2] or 0))
            for row in session.execute(
                select(
                    Submission.problem_id,
                    func.count(Submission.id),
                    func.sum(case((Submission.status == "AC", 1), else_=0)),
                )
                .where(Submission.user_id == user_id)
                .group_by(Submission.problem_id)
            ).all()
        }
        last_by_assignment: dict[int, str] = {}
        for assignment in assignments:
            ids = list(assignment.problem_ids or [])
            if not ids:
                continue
            latest = session.execute(
                select(func.max(Submission.created_at)).where(
                    Submission.user_id == user_id,
                    Submission.problem_id.in_(ids),
                )
            ).scalar()
            if latest is not None:
                last_by_assignment[assignment.id] = latest.isoformat()

        rows: list[StudentAssignmentRow] = []
        for assignment in assignments:
            ids = list(assignment.problem_ids or [])
            submissions = 0
            accepted = 0
            for problem_id in ids:
                count, ok = stats.get(problem_id, (0, 0))
                submissions += int(count or 0)
                accepted += int(ok or 0)
            # `to_dict` is where "overdue" is defined (model reads the
            # clock once, with `now` injectable). Recomputing
            # `due_at < now` here would be a second definition that can
            # disagree with the list view.
            row = assignment.to_dict()
            rows.append(
                StudentAssignmentRow(
                    assignment_id=assignment.id,
                    title=assignment.title,
                    due_at=row["due_at"],
                    overdue=bool(row["overdue"]),
                    problem_count=len(ids),
                    submissions=submissions,
                    accepted=accepted,
                    # Per *problem*, not per submission: a student who
                    # submitted the same problem five times has still
                    # covered one problem.
                    progress=(
                        None
                        if submissions == 0
                        else (accepted / len(ids) if ids else 0.0)
                    ),
                    last_submitted_at=last_by_assignment.get(assignment.id),
                )
            )

        return StudentReportOut(
            user_id=student.id,
            display_name=student.display_name or student.username,
            username=student.username,
            rows=rows,
            completed=sum(1 for r in rows if r.submissions > 0),
            total_assignments=len(rows),
        )


class MyAssignmentOut(BaseModel):
    """One assignment, as the student it was set for sees it.

    Flat rather than nesting an :class:`AssignmentOut`: every field here
    is rendered by the list screen, and a nested object would only add a
    level for the client to reach through.
    """

    id: int
    title: str
    description: str = ""
    due_at: Optional[str] = None
    overdue: bool = False
    assistance_level: int = DEFAULT_ASSISTANCE_LEVEL
    class_id: Optional[int] = None
    #: The class name, for grouping. ``None`` is the "for everyone" kind
    #: — and also every row written before classes existed.
    classroom_name: Optional[str] = None
    problem_ids: list[int] = Field(default_factory=list)
    problem_count: int = 0
    #: Distinct problems passed at least once. Not the AC *count*: a
    #: student who resubmits a fixed solution has still solved one
    #: problem, and counting submissions would let a single problem push
    #: this past ``problem_count``.
    solved: int = 0
    #: Which ones. The count alone cannot tell the list which rows to
    #: tick, and a client inferring it from ``solved`` would be guessing.
    solved_problem_ids: list[int] = Field(default_factory=list)
    #: Submissions made against this assignment's problems.
    submissions: int = 0
    #: ``solved / problem_count``, or ``None`` when nothing was
    #: submitted. ``None`` and ``0.0`` are deliberately distinct:
    #: "has not started" and "started and passed none of them" call for
    #: different things on screen.
    progress: Optional[float] = None
    last_submitted_at: Optional[str] = None


@router.get("/my/assignments", response_model=list[MyAssignmentOut])
def my_assignments(user: User = Depends(require_user)) -> list[MyAssignmentOut]:
    """The assignments this student should see, with their own progress.

    Two kinds are included: homework set for a class they have joined,
    and homework set for nobody in particular. Homework set for a class
    they are **not** in is excluded — that is the entire reason to
    assign something to a class rather than to everyone.

    An assignment with no ``class_id`` stays visible to everybody, which
    is also what keeps this endpoint useful on a database whose existing
    rows all predate classes.
    """
    from datetime import datetime

    from sqlalchemy import case, func, select

    from app.models.submission import Submission

    with get_session() as session:
        my_class_ids = list(
            session.execute(
                select(ClassMember.class_id).where(ClassMember.user_id == user.id)
            )
            .scalars()
            .all()
        )

        stmt = select(Assignment)
        if my_class_ids:
            stmt = stmt.where(
                (Assignment.class_id.is_(None))
                | (Assignment.class_id.in_(my_class_ids))
            )
        else:
            stmt = stmt.where(Assignment.class_id.is_(None))
        rows = session.execute(stmt.order_by(Assignment.id.desc())).scalars().all()

        class_ids = [r.class_id for r in rows if r.class_id is not None]
        names: dict[int, str] = {}
        if class_ids:
            names = {
                room.id: room.name
                for room in session.execute(
                    select(Classroom).where(Classroom.id.in_(class_ids))
                )
                .scalars()
                .all()
            }

        # One grouped query for every problem the student has touched,
        # rather than one per assignment. `max` over a 1/0 case answers
        # "passed at least once" — `sum` would count repeat AC runs.
        per_problem: dict[int, tuple[int, int, Optional[datetime]]] = {
            int(pid): (int(total or 0), int(ok or 0), latest)
            for pid, total, ok, latest in session.execute(
                select(
                    Submission.problem_id,
                    func.count(Submission.id),
                    func.max(case((Submission.status == "AC", 1), else_=0)),
                    func.max(Submission.created_at),
                )
                .where(Submission.user_id == user.id)
                .group_by(Submission.problem_id)
            ).all()
        }

        out: list[MyAssignmentOut] = []
        for row in rows:
            ids = list(row.problem_ids or [])
            submissions = 0
            solved = 0
            solved_ids: list[int] = []
            last: Optional[datetime] = None
            for problem_id in ids:
                total, ok, latest = per_problem.get(int(problem_id), (0, 0, None))
                submissions += total
                if ok:
                    solved += 1
                    solved_ids.append(int(problem_id))
                if latest is not None and (last is None or latest > last):
                    last = latest

            # `to_dict` owns the definition of "overdue" (it reads the
            # clock once, with `now` injectable). Recomputing it here
            # would be a second definition that can disagree with the
            # teacher's list.
            data = row.to_dict()
            out.append(
                MyAssignmentOut(
                    id=row.id,
                    title=row.title,
                    description=row.description,
                    due_at=data["due_at"],
                    overdue=bool(data["overdue"]),
                    assistance_level=row.assistance_level,
                    class_id=row.class_id,
                    classroom_name=names.get(row.class_id) if row.class_id else None,
                    problem_ids=ids,
                    problem_count=len(ids),
                    solved=solved,
                    solved_problem_ids=solved_ids,
                    submissions=submissions,
                    progress=(
                        None
                        if submissions == 0
                        else (solved / len(ids) if ids else 0.0)
                    ),
                    last_submitted_at=last.isoformat() if last else None,
                )
            )
        return out
