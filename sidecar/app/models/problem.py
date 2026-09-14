"""Problem ORM model.

A Problem represents a single algorithmic challenge. Its
``knowledge_tags`` field carries the list of knowledge-point labels
(e.g. "动态规划", "二分查找") used by P1-17's knowledge-statistics
aggregation.

P1-17 only needed the minimal fields required to compute knowledge
statistics: an id, a title, and the tags. PU-11 fills in the fields that
P1-8 reserved, because the problem catalogue is now a first-class
feature rather than a hardcoded constant:

* the statement itself (``description`` plus optional ``input_format`` /
  ``output_format``) — until now the wording lived in ``Practice.vue``;
* ``samples`` — the *display* cases, mirrored into the frontend as a
  read-only preview (the judging cases live in :class:`TestCase`);
* ``time_limit`` / ``memory_limit`` — per-problem judge limits, replacing
  the constants the judge request used to carry;
* ``difficulty`` / ``source`` — catalogue metadata for filtering;
* ``is_active`` — soft delete, so retiring a problem does not orphan the
  submissions and mistake-book entries that point at it.

Why ``samples`` is JSON rather than a list of ``TestCase`` rows
--------------------------------------------------------------
The two collections answer different questions and have different
trust levels. ``samples`` is *presentation*: it belongs to the problem
record, has no ordinal semantics beyond array order, and is safe to
send to the client whole. :class:`TestCase` is *judging*: it is ordered,
carries the hidden/visible distinction, and — per PHASE-UX PU-12 — is
the collection whose full contents must not leak to the client.

Keeping them apart means "show the user the samples" and "judge against
every case" are different queries with different result shapes, instead
of one query that the caller has to remember to filter correctly.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Allowed values of :attr:`Problem.difficulty`. Kept as a module constant
#: so the catalogue loader, the API filter and the tests share one list
#: instead of each spelling the three words out.
DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

#: Default per-problem judge limits. These match the values the judge
#: request used before PU-11 hardcoded them (10s / 256 MB), so a problem
#: that does not state its own limits behaves exactly as it used to.
DEFAULT_TIME_LIMIT = 10.0
DEFAULT_MEMORY_LIMIT = 256


class Problem(Base):
    """A single algorithmic problem.

    Attributes:
        id: Primary key.
        title: Short human-readable title.
        knowledge_tags: List of knowledge-point labels associated with
            this problem. Stored as JSON so the schema stays flexible
            (tags are a free-form list, not a fixed enum).
        description: Markdown statement body. Kept as Markdown rather
            than HTML so the frontend owns rendering.
        input_format: Optional "输入格式" section, split out so the UI can
            style it separately from the body.
        output_format: Optional "输出格式" section, same reasoning.
        samples: Display samples as a JSON list of
            ``{"input": str, "output": str}`` mappings. These are shown
            to the user and are *not* what judging runs against — see the
            module docstring for why the two are separate.
        time_limit: Per-case wall-clock timeout in seconds.
        memory_limit: Per-case memory ceiling in megabytes. A language's
            own allowance is added on top by the runner, so this stays
            language-neutral.
        difficulty: One of :data:`DIFFICULTIES`, or ``None`` when the
            problem has not been classified. Nullable rather than
            defaulting to ``"medium"`` because "unclassified" is a real
            state and silently labelling it medium would be a lie.
        source: Where the problem came from (``"POJ 1000"``, a contest
            name, or nothing at all).
        is_active: Soft-delete flag. Inactive problems stay in the table
            so existing submissions keep resolving their foreign key,
            but the catalogue API filters them out.
    """

    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    knowledge_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    # --- PU-11: statement + display samples ---------------------------------
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_format: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_format: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: JSON list of {"input", "output"} mappings. Defaults to an empty
    #: list rather than NULL so callers never have to null-check it.
    samples: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # --- PU-11: judge limits ------------------------------------------------
    time_limit: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_TIME_LIMIT
    )
    memory_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MEMORY_LIMIT
    )

    # --- PU-11: catalogue metadata -----------------------------------------
    difficulty: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    def to_dict(self, *, include_statement: bool = True) -> dict:
        """JSON-safe dict for API serialization.

        Args:
            include_statement: When ``False``, omit the long prose fields
                (``description`` / ``input_format`` / ``output_format``).
                The list endpoint sets this so a catalogue page does not
                ship every statement over the wire just to render titles.
        """
        data: dict = {
            "id": self.id,
            "title": self.title,
            "knowledge_tags": list(self.knowledge_tags or []),
            "samples": list(self.samples or []),
            "time_limit": self.time_limit,
            "memory_limit": self.memory_limit,
            "difficulty": self.difficulty,
            "source": self.source,
            "is_active": self.is_active,
        }
        if include_statement:
            data["description"] = self.description
            data["input_format"] = self.input_format
            data["output_format"] = self.output_format
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Problem id={self.id} title={self.title!r} tags={self.knowledge_tags}>"
