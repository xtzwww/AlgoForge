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

from app.api.auth import require_teacher
from app.api.mistake_book import get_session
from app.models.assignment import Assignment
from app.models.assignment_stats import compute_assignment_stats
from app.models.problem import Problem
from app.models.user import User

router = APIRouter(prefix="/api/v1", tags=["assignments"])


class CreateAssignmentRequest(BaseModel):
    """What the teacher types.

    ``problem_ids`` is validated against the catalogue rather than
    trusted: a typo would otherwise publish an assignment pointing at a
    problem that does not exist, and it would fail as an empty stats
    screen with nothing anywhere saying why.
    """

    title: str = Field(min_length=1, max_length=128)
    description: str = ""
    problem_ids: list[int] = Field(min_length=1)
    due_at: Optional[str] = None


class AssignmentOut(BaseModel):
    id: int
    title: str
    description: str
    problem_ids: list[int]
    due_at: Optional[str] = None
    overdue: bool = False
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
    """
    with get_session() as session:
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
            created_by=teacher.id,
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
