"""Problem ORM model.

A Problem represents a single algorithmic challenge. Its
``knowledge_tags`` field carries the list of knowledge-point labels
(e.g. "动态规划", "二分查找") used by P1-17's knowledge-statistics
aggregation.

P1-17 only needs the minimal fields required to compute knowledge
statistics: an id, a title, and the tags. Additional fields (description,
samples, time_limit, memory_limit) are reserved for later slices per
VIBECODING.md P1-8.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Problem(Base):
    """A single algorithmic problem.

    Attributes:
        id: Primary key.
        title: Short human-readable title.
        knowledge_tags: List of knowledge-point labels associated with
            this problem. Stored as JSON so the schema stays flexible
            (tags are a free-form list, not a fixed enum).
    """

    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    knowledge_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Problem id={self.id} title={self.title!r} tags={self.knowledge_tags}>"
