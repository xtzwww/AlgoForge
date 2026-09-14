"""TestCase ORM model (PU-11).

A TestCase is one stdin/stdout pair that the judge runs a submission
against. Before PU-11 these lived in two places neither of which was the
database: two display cases were hardcoded in ``Practice.vue`` and the
judging cases travelled in the WebSocket request body. That arrangement
had a structural defect beyond being untidy — *every* case was visible to
the client, so a user could read the expected output and hardcode it.

Moving cases server-side is what makes the hidden/visible distinction
possible, which is why :attr:`TestCase.is_sample` exists:

* ``is_sample = True`` — safe to show. Mirrored into
  :attr:`Problem.samples` for display and included in the problem detail
  response.
* ``is_sample = False`` — judging only. Served exclusively through the
  judging path, never through the catalogue API.

The safety story is only as good as the API that reads it. PU-12 must
keep the hidden rows off ``GET /problems/{id}``; the model enforces
nothing by itself, and per PHASE-UX's security note a single-user local
app is the boundary this design assumes.

Ordinal vs. id ordering
-----------------------
Judging reports "case 1 of 8" to the user, so display order has to be
stable and author-controlled. Insertion order happens to work until a
case is added or removed mid-list, at which point the numbering silently
shifts. :attr:`ordinal` makes the order explicit data instead of an
accident of ``id`` assignment.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TestCase(Base):
    """One stdin/stdout pair used to judge submissions to a problem.

    Attributes:
        id: Primary key.
        problem_id: Foreign key to :class:`Problem`. Indexed because every
            read (catalogue detail, judging, the loader's validation) is
            "the cases for this problem".
        ordinal: 1-based position within the problem. Together with
            ``problem_id`` it is unique, so two cases cannot claim the
            same slot — without that constraint a re-import could leave
            duplicated positions and the reported case number would
            depend on row order.
        input: stdin fed to the program. Empty string means "no input",
            which is a legitimate case for a program that reads nothing.
        expected_output: stdout the program must produce. Normalized
            comparison (trailing whitespace, line endings) is
            :func:`app.sandbox.judge.normalize_output`'s job, so this is
            stored exactly as authored.
        is_sample: Whether the case may be shown to the user. See the
            module docstring.
    """

    __tablename__ = "test_cases"
    __table_args__ = (
        # Two cases on the same problem cannot share a position.
        UniqueConstraint("problem_id", "ordinal", name="uq_test_cases_problem_ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("problems.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_sample: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    def to_dict(self, *, include_input: bool = True) -> dict:
        """JSON-safe dict for API serialization.

        Args:
            include_input: Reserved for callers that want to show a case
                without its input (nothing uses that today; the flag
                exists so the shape is decided by the caller rather than
                by duplicating this method later).
        """
        data: dict = {
            "id": self.id,
            "problem_id": self.problem_id,
            "ordinal": self.ordinal,
            "expected_output": self.expected_output,
            "is_sample": self.is_sample,
        }
        if include_input:
            data["input"] = self.input
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<TestCase id={self.id} problem_id={self.problem_id} "
            f"ordinal={self.ordinal} sample={self.is_sample}>"
        )
