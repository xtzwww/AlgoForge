"""Has this student made *this* mistake before? (阶段 1 · 历史联动反馈)

The feedback the README promises as 「这个错误你上次也犯过」. It is the one
thing the mistake book cannot currently say: every entry is analysed in
isolation, so a student who has mis-handled 边界条件 on four different
problems is told about 边界条件 four times as if each were news.

Scope, and why it is narrower than it sounds
--------------------------------------------
**Cross-problem only.** Repeats on the *same* problem are not counted.
Trying a problem three times and failing is ordinary; the same root cause
resurfacing on three *different* problems is the thing worth naming, and
mixing the two would inflate the count with ordinary retries until it
means nothing.

**One root cause at a time.** Matched on ``agent_error_type`` alone, not
on the knowledge tag. "You keep getting 边界条件 wrong" is actionable;
"you keep getting things wrong in 动态规划" is a restatement of the
profile page, where the tag-level numbers already live.

**Owned rows only.** A submission with no owner gets no recurrence. The
alternative — counting every unowned mistake as one student's history —
would attribute a stranger's errors to whoever happens to be looking,
which is worse than saying nothing.

Read-time, not stored
---------------------
Computed when the analysis lands rather than written to a column. A
stored ``recurrence_count`` would be a number that goes stale the moment
the next mistake arrives, and the only way to keep it honest would be to
update every earlier entry on every new mistake. The count is one indexed
query; a wrong number shown to a student is not.

The Chinese sentence is produced here for the same reason
:mod:`app.process_trace` produces its own: the wording is part of the
feedback, and two places that phrase it would eventually disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.mistake_book import MistakeBookEntry
from app.models.submission import Submission


@dataclass(frozen=True)
class Recurrence:
    """Prior occurrences of one root cause, for one student.

    Attributes:
        count: How many *earlier* entries with this label exist on
            problems other than the current one. ``0`` means this is the
            first — which is not a failure to find anything, it is the
            answer.
        problem_ids: Those entries' problem ids, most recent first, at
            most :data:`SAMPLE_LIMIT`. Enough to say "where", not enough
            to turn the feedback into a reading list.
        last_at: When the most recent prior occurrence was filed, as an
            ISO string, or ``None``.
        label: The root cause these are occurrences *of*, echoed back so
            the caller does not have to carry it alongside.
    """

    label: str
    count: int = 0
    problem_ids: list[int] = field(default_factory=list)
    last_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "count": self.count,
            "problem_ids": list(self.problem_ids),
            "last_at": self.last_at,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        """One sentence, or ``''`` when there is nothing to say.

        Empty rather than "这是你第一次犯这个错": the absence of a
        history is not feedback, and a line that always renders teaches
        the student to skip it — including on the day it finally says
        something.
        """
        if self.count <= 0:
            return ""
        return (
            f"这个错因你之前在别的题上也出现过 {self.count} 次"
            f"（共 {self.count + 1} 次）——不是第一次了，值得单独练一下。"
        )


#: How many prior problem ids to surface. Small on purpose: the sentence
#: is the feedback, and a list of twelve problem numbers under it would
#: turn a nudge into homework.
SAMPLE_LIMIT = 3


def find_recurrence(
    session: Session,
    *,
    user_id: Optional[int],
    error_type: str,
    problem_id: Optional[int],
    exclude_entry_id: Optional[int] = None,
) -> Recurrence:
    """Earlier mistakes with the same label, on other problems.

    Args:
        session: A SQLAlchemy session.
        user_id: Owner to attribute the history to. ``None`` returns an
            empty :class:`Recurrence` — see the module docstring; an
            unowned submission has no history to speak of.
        error_type: The root-cause label to look for.
        problem_id: The problem being analysed *now*. Entries on it are
            excluded, so ordinary retries of one problem do not read as
            a pattern.
        exclude_entry_id: The entry being annotated. It is already
            committed with this label, so it would otherwise match
            itself and every count would be off by one.

    Returns:
        A :class:`Recurrence`. Never raises on missing data — a student
        with no history is the common case, not an error.
    """
    if user_id is None or not error_type:
        return Recurrence(label=error_type or "")

    stmt = (
        select(
            MistakeBookEntry.id,
            MistakeBookEntry.problem_id,
            MistakeBookEntry.created_at,
        )
        .join(Submission, MistakeBookEntry.submission_id == Submission.id)
        .where(Submission.user_id == user_id)
        .where(MistakeBookEntry.agent_error_type == error_type)
        # "Earlier" by id: entries are appended, so a smaller id is an
        # earlier mistake. Comparing on id rather than created_at keeps
        # this correct for rows written in the same transaction, where
        # the timestamps can be identical.
        .order_by(MistakeBookEntry.id.desc())
    )

    if exclude_entry_id is not None:
        stmt = stmt.where(MistakeBookEntry.id != exclude_entry_id)
    if problem_id is not None:
        stmt = stmt.where(MistakeBookEntry.problem_id != problem_id)

    rows = session.execute(stmt).all()
    if not rows:
        return Recurrence(label=error_type)

    # Distinct problems, most recent first: a student who got this wrong
    # twice on one problem is one occurrence of the pattern, not two.
    #
    # The whole set is collected and only *then* trimmed, because
    # ``count`` and ``problem_ids`` answer different questions: the count
    # is how many problems this has happened on, the sample is where to
    # point the student. Stopping at the sample size would report "3
    # times" for a student with nine.
    distinct: list[int] = []
    seen: set[int] = set()
    for row in rows:
        pid = int(row.problem_id)
        if pid in seen:
            continue
        seen.add(pid)
        distinct.append(pid)

    last_at = rows[0].created_at.isoformat() if rows[0].created_at else None
    return Recurrence(
        label=error_type,
        count=len(distinct),
        problem_ids=distinct[:SAMPLE_LIMIT],
        last_at=last_at,
    )


def count_distinct_problems(
    session: Session, *, user_id: int, error_type: str
) -> int:
    """How many distinct problems this student has hit `error_type` on.

    Separate from :func:`find_recurrence` because the teacher's console
    needs the number without the sentence, and a count over the whole
    history is a different question from "what came before this entry".
    """
    value = session.execute(
        select(func.count(func.distinct(MistakeBookEntry.problem_id)))
        .select_from(MistakeBookEntry)
        .join(Submission, MistakeBookEntry.submission_id == Submission.id)
        .where(Submission.user_id == user_id)
        .where(MistakeBookEntry.agent_error_type == error_type)
    ).scalar_one()
    return int(value or 0)
