"""Review ORM model (P1-19).

A ReviewRecord stores the persistent spaced-repetition state for a
single :class:`MistakeBookEntry`. Each time the user reviews the
entry, the SM-2 algorithm (``app.scheduler.sm2``) computes the next
interval and updates this row in place.

P1-19 stores the SRS state directly on the review record rather than
appending a row per review event — only the latest state is needed to
schedule the next review. A historical log of every review event can
be added in S10 if analytics need it; for now, a single updatable row
keeps the API and queries simple.

Fields mirror :class:`app.scheduler.sm2.SrsState` so the pure
algorithm function can read/write the ORM row via ``to_state()`` /
``apply_state()`` without manual field mapping.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.scheduler.sm2 import SrsState


class ReviewRecord(Base):
    """The SRS state for one mistake-book entry.

    Attributes:
        id: Primary key.
        entry_id: Foreign key to :class:`MistakeBookEntry`.
        repetitions: Consecutive successful recalls (reset on fail).
        easiness_factor: SM-2 multiplier (≥ 1.3).
        interval_days: Current scheduled interval in days.
        last_reviewed_at: Timestamp of the most recent review.
        due_at: When the next review is due.
        created_at: When this record was first created.
    """

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mistake_book.id"), nullable=False, unique=True
    )
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    easiness_factor: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.5
    )
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_state(self) -> SrsState:
        """Project the ORM row into a pure :class:`SrsState`."""
        return SrsState(
            repetitions=self.repetitions,
            easiness_factor=self.easiness_factor,
            interval_days=self.interval_days,
            last_reviewed_at=self.last_reviewed_at,
            due_at=self.due_at,
        )

    def apply_state(self, state: SrsState) -> None:
        """Write the SRS state back to the ORM row in place."""
        self.repetitions = state.repetitions
        self.easiness_factor = state.easiness_factor
        self.interval_days = state.interval_days
        self.last_reviewed_at = state.last_reviewed_at
        self.due_at = state.due_at

    def to_dict(self) -> dict:
        """JSON-safe dict for API serialization."""
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "repetitions": self.repetitions,
            "easiness_factor": round(self.easiness_factor, 4),
            "interval_days": self.interval_days,
            "last_reviewed_at": (
                self.last_reviewed_at.isoformat()
                if self.last_reviewed_at is not None
                else None
            ),
            "due_at": self.due_at.isoformat() if self.due_at is not None else None,
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ReviewRecord id={self.id} entry_id={self.entry_id} "
            f"reps={self.repetitions} due_at={self.due_at}>"
        )
