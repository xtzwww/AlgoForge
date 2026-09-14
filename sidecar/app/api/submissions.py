"""Submission history API (PU-13).

- GET /api/v1/submissions
    Paged history. Rows deliberately omit ``code`` / ``stdout`` /
    ``stderr`` — a list view has no room for full source, and including
    it would make every page drag every submission's code across the
    wire.
- GET /api/v1/submissions/{submission_id}
    One submission in full, source and captured output included.

There is no POST. Submissions are written by the judge itself
(``app/records.py``, called from ``app/api/judge.py`` and
``app/api/ws.py``): the client cannot be the source of truth for a
verdict it did not compute. A create endpoint here would be a way to
invent history.

Writes happen on the judging path, not here, which is why this module is
read-only and why ``problem_id`` may be null — see
:class:`app.models.submission.Submission` for the free-run case.

Session handling mirrors the catalogue API: a synchronous session from
:func:`app.db.get_session`, with tests injecting their own.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.submission import STATUSES, Submission

router = APIRouter(prefix="/api/v1/submissions", tags=["submissions"])

#: Hard ceiling on ``page_size``, for the same reason the catalogue has
#: one: without it ``?page_size=1000000`` turns a listing into a memory
#: amplifier.
MAX_PAGE_SIZE = 100


# --- Response schemas --------------------------------------------------


class SubmissionSummaryOut(BaseModel):
    """One history row — no source, no captured output.

    ``exec_time`` / ``memory_used`` are nullable because a CE submission
    runs nothing. A client must render "not measured" rather than
    formatting a null as 0 s / 0 MB.
    """

    id: int
    problem_id: Optional[int] = None
    status: str
    language: str
    exec_time: Optional[float] = None
    memory_used: Optional[int] = None
    created_at: Optional[str] = None


class SubmissionListOut(BaseModel):
    """A page of the submission history."""

    items: list[SubmissionSummaryOut]
    total: int
    page: int
    page_size: int


class SubmissionDetailOut(SubmissionSummaryOut):
    """A submission in full, including the code and the output."""

    code: str
    stdout: str
    stderr: str


# --- Helpers -----------------------------------------------------------


def _get_submission_or_404(session: Session, submission_id: int) -> Submission:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(
            status_code=404, detail=f"submission {submission_id} not found"
        )
    return submission


# --- Routes ------------------------------------------------------------


@router.get("", response_model=SubmissionListOut)
def list_submissions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    problem_id: Optional[int] = Query(
        default=None, description="Only submissions for this problem"
    ),
    status: Optional[str] = Query(
        default=None, description="Verdict filter, e.g. WA"
    ),
    language: Optional[str] = Query(
        default=None, description="Only submissions in this language"
    ),
) -> SubmissionListOut:
    """Return one page of submission history, newest first.

    Newest first because the reason to open this list is almost always
    "what did I just run" — an ascending order would put that on the
    last page.
    """
    if status is not None and status not in STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"status must be one of {', '.join(STATUSES)}; got {status!r}"
            ),
        )

    with get_session() as session:
        filters = []
        if problem_id is not None:
            filters.append(Submission.problem_id == problem_id)
        if status is not None:
            filters.append(Submission.status == status)
        if language is not None:
            filters.append(Submission.language == language)

        total = session.execute(
            select(func.count()).select_from(Submission).where(*filters)
        ).scalar_one()

        rows = (
            session.execute(
                select(Submission)
                .where(*filters)
                # id descending rather than created_at: two submissions
                # in the same second (easy to do from a script) would
                # otherwise come back in an order SQLite and PostgreSQL
                # need not agree on.
                .order_by(Submission.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )

        return SubmissionListOut(
            items=[
                SubmissionSummaryOut(**row.to_dict(include_source=False))
                for row in rows
            ],
            total=int(total),
            page=page,
            page_size=page_size,
        )


@router.get("/{submission_id}", response_model=SubmissionDetailOut)
def get_submission(submission_id: int) -> SubmissionDetailOut:
    """Return one submission with its source and captured output."""
    with get_session() as session:
        submission = _get_submission_or_404(session, submission_id)
        return SubmissionDetailOut(**submission.to_dict())
