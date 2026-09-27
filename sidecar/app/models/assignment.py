"""Homework: a set of problems with a deadline (阶段 4).

An assignment is deliberately small — a title, some words, a list of
problem ids, a due date — because the moment it grows a submission
status, a mark, and a per-student state it becomes the thing the README
promises not to build: full course management. There is no "assigned to"
table, no per-student row, no grade column.

How a student "receives" one, then
----------------------------------
By asking. ``GET /api/v1/assignments`` is open and returns the list;
nothing is copied onto the student. Which means the interesting
question — who has not done it yet? — is answered by *subtraction* at
read time (:func:`compute_assignment_stats` diffs the roster against the
people who submitted), not by a status column that has to be kept in
step with every submission that arrives.

Why ``problem_ids`` is a JSON column here
-----------------------------------------
This project has already shipped two bugs from expanding JSON inside a
query, and :mod:`app.models.tag_values` exists because of them. This
column is safe from that class of bug for a boring reason: nothing ever
filters *inside* it. An assignment is always loaded whole and iterated
in Python. The moment somebody writes ``WHERE :problem_id IN
problem_ids``, this docstring is wrong and the column needs a child
table — so it says so here rather than in a commit message nobody reads.

Why there is no "closed" state
------------------------------
A past due date is a fact about the calendar, not a field to maintain.
Deriving it means an assignment cannot be stuck open because nobody ran
a job to close it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.assistance import DEFAULT_ASSISTANCE_LEVEL, policy_for, policy_to_dict
from app.models.base import Base


class Assignment(Base):
    """A set of problems the teacher wants done by a date.

    Attributes:
        id: Primary key.
        title: What to call it.
        description: Optional words — what to look out for, which
            chapter it goes with.
        problem_ids: The problems, in the order the teacher picked
            them. Stored as JSON; see the module docstring.
        due_at: When it is due, or ``None`` for "no deadline". Nullable
            rather than defaulted to some far-future date: an assignment
            with no deadline is a real thing, and a sentinel date is a
            bug waiting to be compared against.
        created_by: The teacher who published it.
        created_at: When it was published.
        class_id: Which class it was set for, or ``None`` for "anyone".
            Nullable on purpose and for two separate reasons: every
            assignment that existed before classes did is a ``None``, and
            a teacher who wants practice problems for whoever wanders in
            should not have to invent a class to say so.
            ``ondelete="SET NULL"`` because deleting a class must not
            delete the work students did for it — the homework survives
            as an unassigned one, which is a thing that can be read,
            rather than a cascade that erases submissions.
        assistance_level: How much AI help a student may use while
            working on it (1–5). See :mod:`app.assistance` for the five
            levels and what each one permits.

            Stored on the assignment rather than on the problem, and
            that placement is the point: the *same* problem is a
            practice exercise on Monday and an assessment on Friday.
            Putting the level on the problem would force the teacher to
            duplicate it to change that; putting it here makes "this
            problem, no AI today" a property of the task they set.

            Defaults to 3 (hints-only) for rows created before the
            column existed, which is the middle of the scale — a
            historical assignment should not silently become either
            "no help at all" or "AI writes it for you".
    """

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: See the module docstring: never queried inside, so JSON is safe.
    #: ``default=list`` rather than ``[]`` — a mutable default shared
    #: between instances is the kind of bug that only shows up once two
    #: assignments exist.
    problem_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    #: 1–5; see :mod:`app.assistance`. Server default 3 so the migration
    #: can add the column to existing rows without a data backfill.
    assistance_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_ASSISTANCE_LEVEL, server_default="3"
    )
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    #: Which problem set this assignment was drawn from, when it was
    #: published from one (阶段: 题单改造). The homework is still a
    #: *snapshot* — ``problem_ids`` is what the students actually see and
    #: the set can change afterwards without touching this row. This
    #: column only records provenance, so a teacher can later answer
    #: "which assignments came from 二分专题" and stats can be traced back
    #: to a set. ``ondelete="SET NULL"`` because deleting a set must not
    #: take the assignments that cite it down with it.
    source_set_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("problem_sets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: See the class attribute docstring: nullable so pre-class rows stay
    #: valid and so "set for everyone" is expressible without a fake class.
    class_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("classrooms.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    def to_dict(self, *, now: Optional[datetime] = None) -> dict:
        """Serialize for the API.

        Args:
            now: "Today", so ``overdue`` can be computed here rather
                than by every client, which would otherwise each invent
                their own idea of what counts as late.
        """
        if now is None:
            now = datetime.now(UTC).replace(tzinfo=None)
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "problem_ids": list(self.problem_ids or []),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "overdue": self.due_at is not None and self.due_at < now,
            "assistance_level": self.assistance_level,
            "assistance": policy_to_dict(policy_for(self.assistance_level)),
            "created_by": self.created_by,
            "source_set_id": self.source_set_id,
            "class_id": self.class_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Assignment id={self.id} title={self.title!r}>"
