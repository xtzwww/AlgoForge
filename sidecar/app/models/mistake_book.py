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

Fields left to later phases:
- ``agent_generated_variants``: optional variant problems (S7).

P2-4 added ``agent_error_type`` and ``agent_suggestion``. The docstring
above used to list them as pending; they are now real columns, and the
migration ``pu_14_agent_analysis_fields`` creates them.
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
            entry. May be empty. An empty value on a PU-13 auto-archived
            entry is what marks it as not yet analysed by the agent.
        error_cause_fingerprint: Digest of the originating submission's
            (problem_id, language, code), set only on auto-archived
            entries. Used to keep resubmitting the same wrong code from
            creating duplicate entries.
        knowledge_tags: Snapshot of :attr:`Problem.knowledge_tags` at
            the time this entry was created. Stored separately so that
            re-tagging the problem later doesn't retroactively rewrite
            history.
        reviewed: Whether the user has reviewed this entry after the
            latest reset. S10's SM-2 loop reads this.
        review_count: Number of times the entry has been reviewed.
        last_reviewed_at: Timestamp of the most recent review.
        agent_error_type: P2-4's structured root-cause label, or None
            when the entry has not been analysed. See the column comment.
        agent_suggestion: P2-4's prose analysis (detail + hint + fix), or
            "" when the entry has not been analysed.
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
    #: P2-4: the agent's structured root-cause label, from the closed
    #: taxonomy in ``app/eval_cases.py``. Indexed because P2-21 filters
    #: the mistake book by it (``GET /mistakes?error_type=overflow``), and
    #: a LIKE scan over ``error_cause`` would be both slower and unable to
    #: distinguish "labelled overflow" from "the user wrote the word".
    #:
    #: NULL means *not analysed yet* — distinct from an empty string,
    #: which is what ``error_cause`` uses for "not yet written". The label
    #: is either a real member of the taxonomy or absent; there is no
    #: meaningful empty label, so NULL is the honest encoding.
    agent_error_type: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    #: P2-4: the agent's prose — ``error_detail`` + ``hint`` +
    #: ``suggested_fix``, joined for display. Stored as one text column
    #: rather than three because they are always read together (the entry
    #: is shown as a unit) and never queried individually; splitting them
    #: would add two columns and a migration each time the prompt gains a
    #: field.
    agent_suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: PU-13: digest of (problem_id, language, code) for the submission
    #: that produced this entry. Written only for auto-archived entries,
    #: where it lets a resubmission of the same wrong code be recognised
    #: as a duplicate — see ``app/records.py``. User-created entries leave
    #: it NULL and are exempt from that check, because a user adding an
    #: entry by hand is making a deliberate statement.
    error_cause_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
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
        """JSON-safe dict for API serialization.

        ``error_cause_fingerprint`` is deliberately omitted. It is an
        internal dedupe key with no meaning to a client, and publishing it
        would invite a frontend to depend on a value that is only an
        implementation detail of ``app/records.py``.
        """
        return {
            "id": self.id,
            "submission_id": self.submission_id,
            "problem_id": self.problem_id,
            "error_cause": self.error_cause,
            "agent_error_type": self.agent_error_type,
            "agent_suggestion": self.agent_suggestion,
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
