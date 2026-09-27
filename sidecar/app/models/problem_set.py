"""Problem sets (题单/集合): teacher-curated, ordered problem collections.

A ProblemSet is the 力扣/牛客-style layer that sits between the flat
problem catalogue (``problems``) and homework (``assignments``): a named,
ordered, shareable list of problems a teacher maintains once and then
draws on when publishing homework.

Why items are a child table rather than a JSON array on the set
---------------------------------------------------------------
``assignments.problem_ids`` is a JSON column, and its module docstring
says exactly when that stops being safe: *the moment somebody writes
``WHERE :problem_id IN problem_ids``, the column needs a child table*.
A problem set exists for queries like "which sets contain this problem",
"reorder the set", and "count submissions per problem in this set" —
every one of them filters or aggregates *inside* the collection. So the
items are rows, not JSON, with ``position`` as the author-controlled
order (1-based) and a ``(problem_set_id, problem_id)`` unique constraint
so a problem cannot appear twice in one set.

What ``visibility`` means, and why it is three values
-----------------------------------------------------
* ``private`` — only the owning teacher can see it. A teacher's working
  drafts and half-finished 专题 live here.
* ``class`` — bound to a ``class_id``; that class's students can see it
  (and the owning teacher). This is "布置给 25 软件 2 班" for a *set*.
* ``public`` — everyone can see it. The 力扣-style official 题单 and
  teacher-shared collections.

``class_id`` is only meaningful when ``visibility`` is ``class``; the
model does not enforce that relationship, the route layer does, because
a violation is a caller error, not a schema error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Allowed values of :attr:`ProblemSet.visibility`. Kept as a module
#: constant so the API validation, the model and the tests share one
#: list instead of each spelling the three words out.
VISIBILITY_PRIVATE = "private"
VISIBILITY_CLASS = "class"
VISIBILITY_PUBLIC = "public"
VISIBILITIES: tuple[str, ...] = (
    VISIBILITY_PRIVATE,
    VISIBILITY_CLASS,
    VISIBILITY_PUBLIC,
)


class ProblemSet(Base):
    """A named, ordered collection of problems.

    Attributes:
        id: Primary key.
        title: What to call it — ``"二分专题"``, ``"热题 100"``.
        description: Optional words — which chapter it goes with, what
            it is for.
        visibility: One of :data:`VISIBILITIES`.
        class_id: Which class may see it, when ``visibility`` is
            ``"class"``. ``None`` otherwise.
        created_by: The teacher who owns it, or ``None`` for a
            system-seeded set. Ownership is a single column rather than a
            permission list, matching ``Classroom.teacher_id``.
        created_at / updated_at: Server timestamps. ``updated_at`` is
            bumped by the ORM on any update so the student set list can
            sort by "recently touched" without a second query.
        is_active: Soft-delete flag. Inactive sets stay in the table so
            assignments that recorded ``source_set_id`` keep resolving
            their foreign key, but the API filters them out.
    """

    __tablename__ = "problem_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VISIBILITY_PRIVATE
    )
    class_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("classrooms.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    def to_dict(self, *, with_counts: Optional[dict] = None) -> dict:
        """JSON-safe dict for API serialization.

        Args:
            with_counts: Optional ``{"problem_count": int}`` from a query
                that already counted the items, so the list endpoint does
                not run one query per row.
        """
        out: dict = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "visibility": self.visibility,
            "class_id": self.class_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_active": self.is_active,
        }
        if with_counts is not None:
            out["problem_count"] = int(with_counts.get("problem_count", 0))
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProblemSet id={self.id} title={self.title!r} "
            f"visibility={self.visibility!r}>"
        )


class ProblemSetItem(Base):
    """One problem inside one set, at one position.

    Attributes:
        id: Primary key.
        problem_set_id: Which set. ``ondelete="CASCADE"``: deleting a set
            deletes its membership rows, which own nothing and must not
            outlive the set.
        problem_id: Which problem.
        position: The author-controlled order, 1-based. Not unique on its
            own because items are only ever ordered *within* a set; the
            unique pair is ``(problem_set_id, problem_id)`` — a problem
            twice in one set would double-count every stats screen.
        created_at: When the problem was added to the set.
    """

    __tablename__ = "problem_set_items"
    __table_args__ = (
        UniqueConstraint(
            "problem_set_id", "problem_id", name="uq_problem_set_item"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    problem_set_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("problem_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    problem_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("problems.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProblemSetItem set={self.problem_set_id} "
            f"problem={self.problem_id} pos={self.position}>"
        )
