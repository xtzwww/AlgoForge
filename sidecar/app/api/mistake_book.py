"""MistakeBook API endpoints (P1-18).

CRUD for the mistake-book:

- POST   /api/v1/mistake-book
    Create an entry from a submission_id + error_cause text. The
    problem_id and knowledge_tags are derived from the submission's
    problem; the caller doesn't pass them directly.
- GET    /api/v1/mistake-book
    List entries, optionally filtered by ?problem_id= or ?reviewed=.
- GET    /api/v1/mistake-book/{entry_id}
    Fetch a single entry.
- PATCH  /api/v1/mistake-book/{entry_id}
    Update error_cause and/or reviewed flag.
- DELETE /api/v1/mistake-book/{entry_id}
    Remove an entry.

Database wiring is minimal for P1-18: the sidecar creates a single
SQLite session per request. P1-10 / P1-20 will swap this for an async
session against PostgreSQL + Alembic; the route signatures are stable
so the swap is mechanical.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, reset_engine  # noqa: F401  (re-exported)
from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry
from app.models.problem import Problem
from app.models.submission import Submission

router = APIRouter(prefix="/api/v1/mistake-book", tags=["mistake-book"])


# --- Session ------------------------------------------------------------
# The engine/session logic now lives in app/db.py, which is the single
# place that resolves DATABASE_URL (PostgreSQL by default). get_session
# is imported above and re-exported so that existing callers
# (profile.py, review.py, and the tests that monkeypatch it) keep
# working unchanged. New code should import from app.db directly.


# --- Pydantic schemas --------------------------------------------------


class MistakeEntryCreate(BaseModel):
    """Request body for POST /api/v1/mistake-book."""

    submission_id: int
    error_cause: str = Field(default="", max_length=2000)


class MistakeEntryUpdate(BaseModel):
    """Request body for PATCH /api/v1/mistake-book/{id}.

    Both fields are optional; only provided fields are updated.
    """

    error_cause: Optional[str] = Field(default=None, max_length=2000)
    reviewed: Optional[bool] = None


class MistakeEntryOut(BaseModel):
    id: int
    submission_id: int
    problem_id: int
    error_cause: str
    knowledge_tags: list[str]
    reviewed: bool
    review_count: int
    last_reviewed_at: Optional[str] = None
    created_at: Optional[str] = None


# --- Helpers -----------------------------------------------------------


def _entry_to_out(entry: MistakeBookEntry) -> MistakeEntryOut:
    """Serialize an ORM row to the API response model."""
    return MistakeEntryOut(
        id=entry.id,
        submission_id=entry.submission_id,
        problem_id=entry.problem_id,
        error_cause=entry.error_cause,
        knowledge_tags=list(entry.knowledge_tags or []),
        reviewed=entry.reviewed,
        review_count=entry.review_count,
        last_reviewed_at=(
            entry.last_reviewed_at.isoformat()
            if entry.last_reviewed_at is not None
            else None
        ),
        created_at=(
            entry.created_at.isoformat() if entry.created_at is not None else None
        ),
    )


def _get_entry_or_404(session: Session, entry_id: int) -> MistakeBookEntry:
    entry = session.get(MistakeBookEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"entry {entry_id} not found")
    return entry


def _get_submission_or_404(session: Session, submission_id: int) -> Submission:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(
            status_code=404,
            detail=f"submission {submission_id} not found",
        )
    return submission


def _get_problem_or_404(session: Session, problem_id: int) -> Problem:
    problem = session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(
            status_code=404,
            detail=f"problem {problem_id} not found",
        )
    return problem


# --- Routes ------------------------------------------------------------


@router.post("", response_model=MistakeEntryOut, status_code=201)
def create_entry(req: MistakeEntryCreate) -> MistakeEntryOut:
    """Create a mistake-book entry from a submission.

    Pulls the problem_id and knowledge_tags from the submission's
    problem so the caller doesn't have to pass them. The submission's
    status must not be AC — adding an AC submission to the mistake
    book is a client bug.
    """
    with get_session() as session:
        submission = _get_submission_or_404(session, req.submission_id)
        if submission.status == "AC":
            raise HTTPException(
                status_code=400,
                detail="cannot add an AC submission to the mistake book",
            )
        problem = _get_problem_or_404(session, submission.problem_id)

        entry = MistakeBookEntry(
            submission_id=submission.id,
            problem_id=problem.id,
            error_cause=req.error_cause,
            knowledge_tags=list(problem.knowledge_tags or []),
            reviewed=False,
            review_count=0,
            last_reviewed_at=None,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return _entry_to_out(entry)


@router.get("", response_model=list[MistakeEntryOut])
def list_entries(
    problem_id: Optional[int] = Query(default=None),
    reviewed: Optional[bool] = Query(default=None),
) -> list[MistakeEntryOut]:
    """List mistake-book entries, optionally filtered.

    Filters:
        problem_id: only entries for this problem.
        reviewed:   only entries with the reviewed flag set (or not).
    """
    with get_session() as session:
        stmt = select(MistakeBookEntry).order_by(
            MistakeBookEntry.created_at.desc()
        )
        if problem_id is not None:
            stmt = stmt.where(MistakeBookEntry.problem_id == problem_id)
        if reviewed is not None:
            stmt = stmt.where(MistakeBookEntry.reviewed == reviewed)
        rows = session.execute(stmt).scalars().all()
        return [_entry_to_out(e) for e in rows]


@router.get("/{entry_id}", response_model=MistakeEntryOut)
def get_entry(entry_id: int) -> MistakeEntryOut:
    with get_session() as session:
        entry = _get_entry_or_404(session, entry_id)
        return _entry_to_out(entry)


@router.patch("/{entry_id}", response_model=MistakeEntryOut)
def update_entry(entry_id: int, req: MistakeEntryUpdate) -> MistakeEntryOut:
    """Update an entry's error_cause and/or reviewed flag.

    Setting ``reviewed=True`` increments ``review_count`` and stamps
    ``last_reviewed_at``. Setting it back to False does NOT decrement
    the counter (review_count is a lifetime total, not a current flag
    complement).
    """
    with get_session() as session:
        entry = _get_entry_or_404(session, entry_id)

        if req.error_cause is not None:
            entry.error_cause = req.error_cause

        if req.reviewed is not None:
            # Only count a transition False→True as a new review.
            if req.reviewed and not entry.reviewed:
                from datetime import UTC, datetime

                entry.review_count += 1
                entry.last_reviewed_at = datetime.now(UTC).replace(tzinfo=None)
            entry.reviewed = req.reviewed

        session.commit()
        session.refresh(entry)
        return _entry_to_out(entry)


@router.delete("/{entry_id}", status_code=204)
def delete_entry(entry_id: int) -> None:
    with get_session() as session:
        entry = _get_entry_or_404(session, entry_id)
        session.delete(entry)
        session.commit()
