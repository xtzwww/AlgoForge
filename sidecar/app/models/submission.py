"""Submission ORM model.

A Submission records a single attempt by the user on a Problem. The
``status`` field carries the judge verdict (AC / WA / TLE / MLE / CE /
RE) from the sandbox. P1-17's knowledge-statistics aggregation joins
submissions → problems → knowledge_tags to compute per-tag accuracy.

PU-13 filled in the fields P1-9 reserved and added the write path. Before
this the table had no writer at all, while ``MistakeBookEntry`` required
a ``submission_id`` that had to already exist — so the mistake book's
normal path could not be reached. That was gap 7 in ``docs/PHASE-UX.md``.

Why ``problem_id`` is nullable
------------------------------
A free run — code the user is trying out with no problem selected — is a
legitimate thing to record, and it has no problem to point at. Those rows
simply do not participate in the knowledge-tag join in
:mod:`app.models.knowledge_stats`, which is the correct outcome: a
submission that is not about a problem should not move that problem's
accuracy. The alternative, dropping free runs entirely, would leave the
submission history with holes that the user cannot explain.

Why the judge output is stored denormalized
-------------------------------------------
``stdout`` / ``stderr`` are the *first failing* case's output, and
``exec_time`` / ``memory_used`` are the worst measurement across cases.
Storing the per-case detail would need a child table, and nothing reads
that yet; the aggregate is what the submission list shows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Verdicts a submission can carry, matching
#: :class:`app.sandbox.judge.JudgeStatus`. Kept as a tuple here so the API
#: layer can validate a filter without importing the sandbox.
STATUSES: tuple[str, ...] = ("AC", "WA", "TLE", "MLE", "CE", "RE")


class Submission(Base):
    """A single submission of code against a problem.

    Attributes:
        id: Primary key.
        problem_id: Foreign key to :class:`Problem`, or ``None`` for a
            free run with no problem selected.
        status: Judge verdict — one of :data:`STATUSES`.
        code: The source code that was submitted.
        language: Language id it was judged as, e.g. ``"cpp"``. Stored
            because code is not self-describing — the same text is a
            valid C++ program and an invalid Python one.
        stdout: Output from the first case that did not pass, or from
            the last case when everything passed. Empty for CE.
        stderr: Stderr from that same case, or the compiler diagnostics
            for CE.
        exec_time: Worst-case wall-clock seconds across all cases, or
            ``None`` when nothing was measured (e.g. CE ran no cases).
        memory_used: Peak RSS in megabytes across all cases, or ``None``
            when nothing was measured.
        created_at: Server-side timestamp of submission creation.
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("problems.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)

    # --- PU-13: what was submitted and how it went ---
    code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str] = mapped_column(
        String(32), nullable=False, default="cpp", server_default="cpp"
    )
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Nullable rather than defaulted to 0: CE never runs a case, so there
    # is no execution to measure, and 0.0 seconds / 0 MB would read as a
    # real measurement of an instant, memory-free run.
    exec_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_dict(self, *, include_source: bool = True) -> dict:
        """Serialize for the API.

        Args:
            include_source: Include ``code`` / ``stdout`` / ``stderr``.
                False for a list view, which has no room for full source
                and would otherwise drag every submission's code across
                the wire.
        """
        data: dict = {
            "id": self.id,
            "problem_id": self.problem_id,
            "status": self.status,
            "language": self.language,
            "exec_time": self.exec_time,
            "memory_used": self.memory_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_source:
            data["code"] = self.code
            data["stdout"] = self.stdout
            data["stderr"] = self.stderr
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Submission id={self.id} problem_id={self.problem_id} "
            f"status={self.status!r}>"
        )
