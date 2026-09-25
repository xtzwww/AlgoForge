"""Review API endpoints (P1-19).

CRUD for the spaced-repetition state attached to mistake-book entries:

- GET    /api/v1/review/due
    List review records whose ``due_at`` has passed (or that have
    never been reviewed). The frontend shows these as "今天要复习".
- GET    /api/v1/review/{entry_id}
    Fetch the SRS state for a specific mistake-book entry. Returns
    404 if no review record exists yet.
- POST   /api/v1/review/{entry_id}/review
    Record a review with a ``quality`` score (0–5). Runs the SM-2
    algorithm, updates the row, returns the new state.
- POST   /api/v1/review/{entry_id}/reset
    Reset the SRS state to defaults (repetitions=0, EF=2.5, interval=0,
    due_at=now). Used when the user wants to restart a schedule.

The session handling mirrors P1-18's mistake-book API (sync, SQLite
fallback in dev).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import get_session
from app.models.mistake_book import MistakeBookEntry
from app.models.review import ReviewRecord
from app.scheduler.sm2 import SrsState, is_due, schedule

router = APIRouter(prefix="/api/v1/review", tags=["review"])


# --- Response schemas --------------------------------------------------


class ReviewStateOut(BaseModel):
    id: int
    entry_id: int
    repetitions: int
    easiness_factor: float
    interval_days: int
    last_reviewed_at: Optional[str] = None
    due_at: Optional[str] = None
    created_at: Optional[str] = None


class ReviewRequest(BaseModel):
    """Request body for POST /api/v1/review/{entry_id}/review."""

    quality: int = Field(ge=0, le=5, description="Recall quality 0-5")


# --- Helpers -----------------------------------------------------------


def _record_to_out(rec: ReviewRecord) -> ReviewStateOut:
    return ReviewStateOut(
        id=rec.id,
        entry_id=rec.entry_id,
        repetitions=rec.repetitions,
        easiness_factor=round(rec.easiness_factor, 4),
        interval_days=rec.interval_days,
        last_reviewed_at=(
            rec.last_reviewed_at.isoformat()
            if rec.last_reviewed_at is not None
            else None
        ),
        due_at=rec.due_at.isoformat() if rec.due_at is not None else None,
        created_at=(
            rec.created_at.isoformat() if rec.created_at is not None else None
        ),
    )


def _get_entry_or_404(session, entry_id: int) -> MistakeBookEntry:
    entry = session.get(MistakeBookEntry, entry_id)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"entry {entry_id} not found"
        )
    return entry


def _get_or_create_review_record(
    session, entry_id: int
) -> ReviewRecord:
    """Return the existing review row, or create a fresh default row."""
    rec = session.execute(
        select(ReviewRecord).where(ReviewRecord.entry_id == entry_id)
    ).scalar_one_or_none()
    if rec is not None:
        return rec
    rec = ReviewRecord(
        entry_id=entry_id,
        repetitions=0,
        easiness_factor=2.5,
        interval_days=0,
        last_reviewed_at=None,
        due_at=None,
    )
    session.add(rec)
    session.flush()  # assign id
    return rec


# --- Routes ------------------------------------------------------------


@router.get("/due", response_model=list[ReviewStateOut])
def list_due(
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ReviewStateOut]:
    """List review records whose next review is due (or never started).

    Records are sorted by ``due_at`` ascending; null due_at comes first
    (those are "never reviewed" — most urgent).
    """
    with get_session() as session:
        rows = session.execute(
            select(ReviewRecord).order_by(ReviewRecord.due_at.asc().nulls_first())
        ).scalars().all()
        now = datetime.now(UTC).replace(tzinfo=None)
        due_rows = [r for r in rows if is_due(r.to_state(), now=now)]
        return [_record_to_out(r) for r in due_rows[:limit]]


@router.get("/{entry_id}", response_model=ReviewStateOut)
def get_review(entry_id: int) -> ReviewStateOut:
    """Fetch the SRS state for a mistake-book entry.

    Returns 404 if no review record exists. Use POST /review to create
    one implicitly via the first review.
    """
    with get_session() as session:
        _get_entry_or_404(session, entry_id)  # ensures entry exists
        rec = session.execute(
            select(ReviewRecord).where(ReviewRecord.entry_id == entry_id)
        ).scalar_one_or_none()
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail=f"no review record for entry {entry_id}",
            )
        return _record_to_out(rec)


@router.post("/{entry_id}/review", response_model=ReviewStateOut)
def record_review(entry_id: int, req: ReviewRequest) -> ReviewStateOut:
    """Record a review for an entry and recompute its schedule.

    Creates a fresh review record on the first review (repetitions=0,
    EF=2.5). Subsequent calls advance the state via the SM-2 algorithm.
    """
    with get_session() as session:
        _get_entry_or_404(session, entry_id)
        rec = _get_or_create_review_record(session, entry_id)

        now = datetime.now(UTC).replace(tzinfo=None)
        new_state = schedule(rec.to_state(), req.quality, now=now)
        rec.apply_state(new_state)
        session.commit()
        session.refresh(rec)
        return _record_to_out(rec)


@router.post("/{entry_id}/reset", response_model=ReviewStateOut)
def reset_review(entry_id: int) -> ReviewStateOut:
    """Reset the SRS state for an entry to defaults."""
    with get_session() as session:
        _get_entry_or_404(session, entry_id)
        rec = _get_or_create_review_record(session, entry_id)
        rec.apply_state(SrsState())
        session.commit()
        session.refresh(rec)
        return _record_to_out(rec)
