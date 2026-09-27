"""Class-wide aggregations for the teacher console (阶段 3).

What a teacher actually asks
----------------------------
The console is not a report card and not an admin panel — it answers
three questions, and every function here maps to one of them:

* **"Did they get what I taught?"** → :func:`compute_error_distribution`
  and :func:`compute_knowledge_stats` with no ``user_id``.
* **"Who is struggling?"** → :func:`compute_student_summaries`.
* **"What should I teach next?"** → the same distribution, read as a
  ranking rather than as a count.

The third one is why the distribution is returned **sorted by count
descending** rather than by label: "全班 60% 的 WA 都是边界条件" is a
statement about the top of that list, and a list ordered alphabetically
makes the teacher find it themselves.

Why these are here and not in the route module
----------------------------------------------
AGENTS.md keeps route modules free of business logic. More practically,
these are the queries most likely to be needed by two callers (the
console and, later, an export), and a query written twice is a query
that answers two different questions.

All of them are plain SQL aggregates — no JSON expansion, no dialect
branching beyond what :mod:`app.models.tag_values` already handles —
which is deliberate: the project has already shipped one bug caused by
a dialect-specific function that every test on SQLite happily accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.mistake_book import MistakeBookEntry
from app.models.submission import REVIEW_DISMISSED, Submission
from app.models.user import ROLE_STUDENT, User


def _open_flag():
    """The SQL predicate for "still in the teacher's queue" (阶段 5).

    Flagged, and not judged to be a false positive. Mirrors
    :attr:`Submission.is_open_flag` — the same rule has to exist twice
    because one version is for rows already in memory and this one is for
    rows still in the database. Note that it must be *both* halves:
    filtering on the review alone would put every unflagged submission
    in the queue.

    Only *dismissed* leaves the queue; a confirmed flag stays. That is
    deliberate: dismissing is the teacher saying "this is noise", and
    noise is the only thing that has to stop asking for attention. A
    confirmed flag is the opposite — a case they may still want in front
    of them — and hiding it the moment it is confirmed would make the
    list vanish under them as they work through it.

    ``is_distinct_from`` rather than ``!=``: an unreviewed row has
    ``process_review IS NULL``, and ``NULL != 'dismissed'`` is NULL —
    which is not true — so a plain ``!=`` would drop every unreviewed row
    and leave the queue permanently empty. ``IS DISTINCT FROM`` treats
    NULL as a comparable value, which is what "not dismissed" means here.
    """
    return and_(
        or_(
            Submission.process_flagged.is_(True),
            Submission.style_flagged.is_(True),
        ),
        Submission.process_review.is_distinct_from(REVIEW_DISMISSED),
    )


@dataclass(frozen=True)
class ErrorCount:
    """How often one root-cause label appears.

    Attributes:
        label: The root-cause identifier, or ``None`` for entries whose
            analysis has not landed or failed.
        description: The same label in Chinese, or ``''`` when unknown.
        count: How many mistake-book entries carry it.
        share: ``count`` over the total entries counted, 0.0 when there
            are none.
    """

    label: Optional[str]
    description: str
    count: int
    share: float

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "description": self.description,
            "count": self.count,
            "share": self.share,
        }


@dataclass(frozen=True)
class StudentSummary:
    """One student, as a teacher needs to see them.

    Attributes:
        user_id / username / display_name: Who.
        submissions: How many they have made.
        accepted: How many were AC.
        accuracy: ``accepted / submissions``, 0.0 when they have made
            none. Zero rather than ``None``: "no attempts yet" and "0%
            correct" are the same decision for a teacher — both mean
            *look at this one* — and a null would just have to be
            handled at the top of the sort.
        mistakes: Open mistake-book entries.
        flagged_submissions: Submissions whose editing pattern was
            flagged (C1) **and not dismissed on review**. The dismissal
            is what makes this number worth reading: a teacher who has
            ruled on four of six flags is looking at two, and a count
            that kept all six would have them re-reading the four they
            already decided about.
        weakest_tag: The knowledge tag with the most non-AC attempts, or
            ``None`` when there are none. This is the console's answer to
            "who is struggling", and it is the only field here that is
            not a plain count.
    """

    user_id: int
    username: str
    display_name: str
    submissions: int
    accepted: int
    accuracy: float
    mistakes: int
    flagged_submissions: int
    weakest_tag: Optional[str]

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "submissions": self.submissions,
            "accepted": self.accepted,
            "accuracy": self.accuracy,
            "mistakes": self.mistakes,
            "flagged_submissions": self.flagged_submissions,
            "weakest_tag": self.weakest_tag,
        }


@dataclass(frozen=True)
class ClassOverview:
    """The numbers at the top of the console.

    Attributes:
        students: Accounts with the student role.
        submissions / accepted / accuracy: Across everyone.
        open_mistakes: Mistake-book entries not yet reviewed.
        flagged_submissions: Rows whose process was flagged (C1). Every
            one of them, whether reviewed or not — this is a measurement,
            and narrowing it to the queue would make the flag look less
            trigger-happy than it is.
        open_flags: The same rows minus the ones a teacher dismissed,
            i.e. the length of the review queue. Both are reported
            because they answer different questions: the first is "does
            my detector over-fire", the second is "do I have anything to
            read before class".
        analysed_mistakes: Entries whose analysis has landed, over the
            entries that exist. Kept separate from ``open_mistakes``
            because a teacher who sees "12 mistakes" should be able to
            tell how many of them have a diagnosis attached.
    """

    students: int
    submissions: int
    accepted: int
    accuracy: float
    open_mistakes: int
    analysed_mistakes: int
    flagged_submissions: int
    open_flags: int

    def to_dict(self) -> dict:
        return {
            "students": self.students,
            "submissions": self.submissions,
            "accepted": self.accepted,
            "accuracy": self.accuracy,
            "open_mistakes": self.open_mistakes,
            "analysed_mistakes": self.analysed_mistakes,
            "flagged_submissions": self.flagged_submissions,
            "open_flags": self.open_flags,
        }


def compute_error_distribution(
    session: Session, user_id: Optional[int] = None
) -> list[ErrorCount]:
    """Count mistake-book entries per root-cause label.

    Args:
        session: A SQLAlchemy session.
        user_id: Restrict to one student. The join to ``submissions`` is
            how ownership reaches the mistake book — entries carry
            ``submission_id`` but no ``user_id`` of their own, and
            duplicating the owner onto both tables would be two places
            to keep in step for no query's benefit.

    Returns:
        One :class:`ErrorCount` per label, **most common first**. Labels
        with no entries are absent rather than present at zero: the
        twelve-label set is a closed list the client already has, and a
        row of zeros would push the interesting part of the answer below
        the fold.

        Entries with no analysis yet are returned as one group with
        ``label=None``, so "not analysed" is visible instead of being
        silently dropped — a console that hid them would make an
        analysis outage look like a quiet week.
    """
    from app.eval_cases import ROOT_CAUSE_DESCRIPTIONS

    stmt = (
        select(
            MistakeBookEntry.agent_error_type.label("label"),
            func.count().label("count"),
        )
        .select_from(MistakeBookEntry)
        .join(Submission, MistakeBookEntry.submission_id == Submission.id)
        .group_by(MistakeBookEntry.agent_error_type)
        .order_by(func.count().desc())
    )
    if user_id is not None:
        stmt = stmt.where(Submission.user_id == user_id)

    rows = session.execute(stmt).all()
    total = sum(int(r.count or 0) for r in rows)

    return [
        ErrorCount(
            label=row.label,
            description=ROOT_CAUSE_DESCRIPTIONS.get(row.label or "", ""),
            count=int(row.count or 0),
            share=round(int(row.count or 0) / total, 4) if total else 0.0,
        )
        for row in rows
    ]


def compute_student_summaries(session: Session) -> list[StudentSummary]:
    """One row per student, worst first.

    Sorted by accuracy ascending so the top of the list is who the
    teacher should look at — which is the only ordering a teacher
    asked "who needs me" will use. Ties break on more submissions
    first: somebody who has tried many times and is still failing has
    been stuck longer than somebody who tried once.

    Returns:
        One :class:`StudentSummary` per student account. Students with
        no submissions are included with zeros: a teacher notices an
        absence, and a row that vanished because it had nothing to
        count would hide exactly the student who has not started.
    """
    is_ac = case((Submission.status == "AC", 1), else_=0)
    flagged = case((_open_flag(), 1), else_=0)

    counts = (
        select(
            Submission.user_id.label("user_id"),
            func.count().label("submissions"),
            func.sum(is_ac).label("accepted"),
            func.sum(flagged).label("flagged"),
        )
        .where(Submission.user_id.is_not(None))
        .group_by(Submission.user_id)
    )
    by_user = {
        row.user_id: (
            int(row.submissions or 0),
            int(row.accepted or 0),
            int(row.flagged or 0),
        )
        for row in session.execute(counts).all()
    }

    mistake_counts = (
        select(
            Submission.user_id.label("user_id"),
            func.count().label("mistakes"),
        )
        .select_from(MistakeBookEntry)
        .join(Submission, MistakeBookEntry.submission_id == Submission.id)
        .where(Submission.user_id.is_not(None))
        .group_by(Submission.user_id)
    )
    mistakes_by_user = {
        row.user_id: int(row.mistakes or 0)
        for row in session.execute(mistake_counts).all()
    }

    students = session.execute(
        select(User).where(User.role == ROLE_STUDENT).order_by(User.display_name)
    ).scalars().all()

    summaries = []
    for student in students:
        submissions, accepted, flagged_count = by_user.get(student.id, (0, 0, 0))
        summaries.append(
            StudentSummary(
                user_id=student.id,
                username=student.username,
                display_name=student.display_name,
                submissions=submissions,
                accepted=accepted,
                accuracy=round(accepted / submissions, 4) if submissions else 0.0,
                mistakes=mistakes_by_user.get(student.id, 0),
                flagged_submissions=flagged_count,
                weakest_tag=_weakest_tag_for(session, student.id),
            )
        )

    summaries.sort(key=lambda s: (s.accuracy, -s.submissions, s.display_name))
    return summaries


def _weakest_tag_for(session: Session, user_id: int) -> Optional[str]:
    """The tag this student has failed most often.

    Counted over **unsuccessful attempts**, not over the accuracy ratio.
    A student who tried 动态规划 twice and failed both needs attention
    more than one who tried 排序 ten times and failed four, even though
    the second has the worse-looking percentage — and a teacher reading
    "42%" does not know how many attempts are behind it.

    A separate query per student, which is the wrong shape at scale and
    the right one here: the console shows this for a class of tens, and
    a single query returning "the most-failed tag per user" needs a
    window function or a dialect-specific ``DISTINCT ON`` — neither of
    which is worth the portability risk for a screen that renders once.
    """
    from app.models.knowledge_stats import knowledge_tag_values
    from app.models.problem import Problem

    tag_value = knowledge_tag_values(session)
    failed = case((Submission.status != "AC", 1), else_=0)

    stmt = (
        select(tag_value.c.value.label("tag"), func.sum(failed).label("failures"))
        .select_from(Submission)
        .join(Problem, Submission.problem_id == Problem.id)
        .where(Submission.user_id == user_id)
        .group_by(tag_value.c.value)
        .order_by(func.sum(failed).desc(), tag_value.c.value)
    )
    row = session.execute(stmt).first()
    return str(row.tag) if row is not None else None


def compute_class_overview(session: Session) -> ClassOverview:
    """The numbers at the top of the console."""
    is_ac = case((Submission.status == "AC", 1), else_=0)
    flagged = case((Submission.process_flagged.is_(True), 1), else_=0)
    open_flags = case((_open_flag(), 1), else_=0)

    totals = session.execute(
        select(
            func.count().label("submissions"),
            func.sum(is_ac).label("accepted"),
            func.sum(flagged).label("flagged"),
            func.sum(open_flags).label("open"),
        ).select_from(Submission)
    ).one()
    submissions = int(totals.submissions or 0)
    accepted = int(totals.accepted or 0)

    students = session.execute(
        select(func.count()).select_from(User).where(User.role == ROLE_STUDENT)
    ).scalar_one()

    mistakes = session.execute(
        select(
            func.count().label("total"),
            func.sum(case((MistakeBookEntry.reviewed.is_(True), 0), else_=1)).label(
                "open"
            ),
            func.sum(
                case((MistakeBookEntry.agent_error_type.is_not(None), 1), else_=0)
            ).label("analysed"),
        ).select_from(MistakeBookEntry)
    ).one()

    return ClassOverview(
        students=int(students or 0),
        submissions=submissions,
        accepted=accepted,
        accuracy=round(accepted / submissions, 4) if submissions else 0.0,
        open_mistakes=int(mistakes.open or 0),
        analysed_mistakes=int(mistakes.analysed or 0),
        flagged_submissions=int(totals.flagged or 0),
        open_flags=int(totals.open or 0),
    )


def list_flagged_submissions(
    session: Session, limit: int = 50, *, include_reviewed: bool = False
) -> list[dict]:
    """Submissions whose editing pattern was flagged (C1).

    The teacher's "过程特征" list. Returns the stored row plus the
    evidence, which is recomputed at read time from the stored numbers
    — see ``Submission.process_view`` for why the words are not persisted.

    Args:
        session: A SQLAlchemy session.
        limit: Cap on rows. A console page, not an export.
        include_reviewed: Also return flags a teacher has already ruled
            on. Off by default, because the point of ruling on one is
            that it stops asking for attention; a queue that kept
            finished work on it is a queue that gets ignored. It stays
            available because "what did I decide last week" is a real
            question and losing the answer to it would be worse than
            showing a longer list.

    Returns:
        One dict per flagged submission, newest first, each carrying the
        owner's display name when there is one.
    """
    stmt = select(Submission).order_by(Submission.id.desc()).limit(limit)
    stmt = (
        stmt.where(
            or_(
                Submission.process_flagged.is_(True),
                Submission.style_flagged.is_(True),
            )
        )
        if include_reviewed
        else stmt.where(_open_flag())
    )
    rows = session.execute(stmt).scalars().all()

    out = []
    for row in rows:
        owner = session.get(User, row.user_id) if row.user_id is not None else None
        data = row.to_dict(include_source=False)
        data["process"] = row.process_view()
        data["style"] = row.style_view()
        data["student"] = owner.display_name if owner is not None else None
        out.append(data)
    return out
