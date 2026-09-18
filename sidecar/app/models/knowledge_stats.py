"""Knowledge-point accuracy statistics (P1-17).

Aggregates :class:`Submission` records by knowledge tag (via the
related :class:`Problem`) and computes per-tag accuracy.

A submission is "correct" iff its status is ``"AC"``. Non-AC verdicts
(WA / TLE / MLE / CE / RE) all count as incorrect for the purposes of
accuracy — P1-17 only cares whether the user got the problem right,
not why they got it wrong (the why is the agent's job in Phase 2).

The aggregation is implemented as a SQL JOIN + GROUP BY on the
database side, so it stays efficient as the submission count grows.
The result is returned as a list of :class:`KnowledgeStat` dataclasses
that the API layer (P1-20) can serialize directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.problem import Problem
from app.models.submission import Submission
from app.models.tag_values import knowledge_tag_values


@dataclass(frozen=True)
class KnowledgeStat:
    """Aggregated accuracy for a single knowledge tag.

    Attributes:
        tag: The knowledge-point label (e.g. "动态规划").
        total: Total submissions on problems tagged with this tag.
        passed: Submissions on those problems that received AC.
        accuracy: ``passed / total`` in the range [0.0, 1.0].
            Zero when there are no submissions.
    """

    tag: str
    total: int
    passed: int
    accuracy: float

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "total": self.total,
            "passed": self.passed,
            "accuracy": self.accuracy,
        }


def compute_knowledge_stats(
    session: Session, user_id: Optional[int] = None
) -> list[KnowledgeStat]:
    """Aggregate per-tag accuracy across all submissions.

    Joins ``submissions`` to ``problems`` and explodes
    ``problems.knowledge_tags`` (a JSON list) into one row per
    (submission, tag) pair, then groups by tag and counts AC vs total.

    Args:
        session: A SQLAlchemy session (sync). The async variant for
            the production API is deferred to P1-20.
        user_id: Restrict to one student's submissions. ``None`` — the
            default, and the pre-existing behaviour — aggregates over
            everyone, which is what the student's own profile page wants
            and what the teacher's class-wide view wants.

            The teacher's *per-student* drill-down passes an id. Note
            that ``None`` deliberately does not mean "unowned rows": a
            submission with no owner is excluded from every student's
            view, and lumping them into the class total is a separate
            question the total is not trying to answer.

    Returns:
        A list of :class:`KnowledgeStat`, one per tag that has at least
        one submission, sorted by tag name ascending. Tags with zero
        submissions are not included (there is nothing to aggregate).
    """
    # One row per tag. The spelling is dialect-specific and lives in
    # ``app.models.tag_values``.
    #
    # This used to call ``func.json_each`` directly, under a comment
    # asserting that "PostgreSQL 16+ supports JSON_TABLE (a superset)".
    # That was wrong: PostgreSQL has no ``json_each`` at all — it is
    # SQLite's name. The profile page would therefore have failed with
    # ``function json_each(json) does not exist`` the first time it ran
    # against the database AGENTS.md mandates, and no test could have
    # caught it, because the whole suite runs on SQLite. See
    # ``app.models.tag_values`` for the account and the fix.
    tag_value = knowledge_tag_values(session)

    is_ac = case((Submission.status == "AC", 1), else_=0)

    stmt = (
        select(
            tag_value.c.value.label("tag"),
            func.count().label("total"),
            func.sum(is_ac).label("passed"),
        )
        .select_from(Submission)
        .join(Problem, Submission.problem_id == Problem.id)
        .group_by(tag_value.c.value)
        .order_by(tag_value.c.value)
    )
    if user_id is not None:
        stmt = stmt.where(Submission.user_id == user_id)

    rows = session.execute(stmt).all()
    results: list[KnowledgeStat] = []
    for row in rows:
        total = int(row.total or 0)
        passed = int(row.passed or 0)
        accuracy = (passed / total) if total > 0 else 0.0
        # The expansion yields the element value. SQLite's json_each types
        # it str or int depending on the JSON; PostgreSQL's
        # json_array_elements_text is always text. Coerce either way —
        # knowledge tags are always strings in this domain.
        results.append(
            KnowledgeStat(
                tag=str(row.tag),
                total=total,
                passed=passed,
                accuracy=round(accuracy, 4),
            )
        )
    return results


def compute_knowledge_stats_for(
    session: Session, tags: Sequence[str], user_id: Optional[int] = None
) -> list[KnowledgeStat]:
    """Aggregate accuracy restricted to a specific set of tags.

    Convenience for callers that want to query a subset (e.g. "show me
    accuracy for the tags on the current problem only"). Tags with no
    submissions are still returned (with total=0, accuracy=0.0) so the
    caller can render a stable radar chart that doesn't lose axes.

    Args:
        session: A SQLAlchemy session.
        tags: The tags to compute stats for. Order is preserved in the
            returned list.
        user_id: Same restriction as :func:`compute_knowledge_stats`.

    Returns:
        One :class:`KnowledgeStat` per requested tag, in the same
        order as ``tags``.
    """
    if not tags:
        return []

    full = {s.tag: s for s in compute_knowledge_stats(session, user_id=user_id)}
    return [
        full.get(
            tag,
            KnowledgeStat(tag=tag, total=0, passed=0, accuracy=0.0),
        )
        for tag in tags
    ]
