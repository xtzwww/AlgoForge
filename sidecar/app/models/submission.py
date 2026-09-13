"""Submission ORM model.

A Submission records a single attempt by the user on a Problem. The
``status`` field carries the judge verdict (AC / WA / TLE / MLE / CE /
RE) from the sandbox. P1-17's knowledge-statistics aggregation joins
submissions → problems → knowledge_tags to compute per-tag accuracy.

P1-17 only needs the fields required for statistics: id, problem_id,
status, created_at. Other fields (code, stdout, stderr, exec_time,
memory_used) are reserved for later slices per VIBECODING.md P1-9.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Submission(Base):
    """A single submission of code against a problem.

    Attributes:
        id: Primary key.
        problem_id: Foreign key to :class:`Problem`.
        status: Judge verdict — one of "AC", "WA", "TLE", "MLE",
            "CE", "RE".
        created_at: Server-side timestamp of submission creation.
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("problems.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Submission id={self.id} problem_id={self.problem_id} status={self.status!r}>"
