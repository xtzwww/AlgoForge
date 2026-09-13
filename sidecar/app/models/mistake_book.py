"""MistakeBook ORM model (P1-18).

A MistakeBookEntry is a curated record of a wrong submission that the
user (or the agent, in Phase 2) decided to keep for later review. It
references the originating :class:`Submission` and stores the agent's
error-cause analysis fields that P1-18 leaves blank (those land in
Phase 2 / S2 when the LLM agent is wired up).

P1-18 scope — model + CRUD API only:
- ``error_cause``: free-form text (operator wrote wrong? edge case?)
- ``knowledge_tags``: snapshot of the problem's tags at the time of
  entry creation, so re-tagging the problem later doesn't retroactively
  change history.
- ``reviewed`` / ``review_count`` / ``last_reviewed_at``: scaffolding
  for the SM-2 spaced-repetition loop (S10). P1-18 only seeds defaults;
  the actual schedule is computed in S10.

Fields left to Phase 2 (S2 Agent):
- ``agent_error_type``: structured label from the LLM (e.g. "边界遗漏")
- ``agent_suggestion``: the agent's fix hint
- ``agent_generated_variants``: optional variant problems
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MistakeBookEntry(Base):
    """A single mistake-book entry derived from a wrong submission.

    Attributes:
        id: Primary key.
        submission_id: Foreign key to the originating :class:`Submission`.
        problem_id: Denormalized from submission for cheaper queries
            (avoid join when listing by problem).
        error_cause: Free-form text the user writes when adding the
            entry. May be empty.
        knowledge_tags: Snapshot of :attr:`Problem.knowledge_tags` at
            the time this entry was created. Stored separately so that
            re-tagging the problem later doesn't retroactively rewrite
            history.
        reviewed: Whether the user has reviewed this entry after the
            latest reset. S10's SM-2 loop reads this.
        review_count: Number of times the entry has been reviewed.
        last_reviewed_at: Timestamp of the most recent review.
        created_at: When this entry was added to the mistake book.
    """

    __tablename__ = "mistake_book"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("submissions.id"), nullable=False
    )
    problem_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("problems.id"), nullable=False
    )
    error_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    knowledge_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    reviewed: Mapped[bool] = mapped_column(default=False, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        """JSON-safe dict for API serialization."""
        return {
            "id": self.id,
            "submission_id": self.submission_id,
            "problem_id": self.problem_id,
            "error_cause": self.error_cause,
            "knowledge_tags": list(self.knowledge_tags or []),
            "reviewed": self.reviewed,
            "review_count": self.review_count,
            "last_reviewed_at": (
                self.last_reviewed_at.isoformat()
                if self.last_reviewed_at is not None
                else None
            ),
            "created_at": (
                self.created_at.isoformat() if self.created_at is not None else None
            ),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<MistakeBookEntry id={self.id} problem_id={self.problem_id} "
            f"reviewed={self.reviewed}>"
        )
