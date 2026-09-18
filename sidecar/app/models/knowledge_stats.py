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
from typing import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.problem import Problem
from app.models.submission import Submission


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


def compute_knowledge_stats(session: Session) -> list[KnowledgeStat]:
    """Aggregate per-tag accuracy across all submissions.

    Joins ``submissions`` to ``problems`` and explodes
    ``problems.knowledge_tags`` (a JSON list) into one row per
    (submission, tag) pair, then groups by tag and counts AC vs total.

    Args:
        session: A SQLAlchemy session (sync). The async variant for
            the production API is deferred to P1-20.

    Returns:
        A list of :class:`KnowledgeStat`, one per tag that has at least
        one submission, sorted by tag name ascending. Tags with zero
        submissions are not included (there is nothing to aggregate).
    """
    # Use JSON_EACH to explode the tags array. SQLAlchemy 2.0 exposes
    # this via func.json_each(...).table_valued(..., joins_implicitly=True)
    # which renders as a LATERAL join against the problems row.
    #
    # SQLite supports JSON_EACH natively; PostgreSQL 16+ supports
    # JSON_TABLE (a superset). For P1-17 we test against SQLite, and
    # the production target (PostgreSQL 16) supports both. When the
    # production DB is wired up (P1-10 Alembic + P1-20 API), this
    # expression may need to switch to func.jsonb_array_elements for
    # PostgreSQL < 16 compatibility.
    tag_value = func.json_each(Problem.knowledge_tags).table_valued(
        "value", joins_implicitly=True
    )

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

    rows = session.execute(stmt).all()
    results: list[KnowledgeStat] = []
    for row in rows:
        total = int(row.total or 0)
        passed = int(row.passed or 0)
        accuracy = (passed / total) if total > 0 else 0.0
        # json_each returns the element value; on SQLite the type may
        # be str or int depending on the JSON. Coerce to str for the
        # tag label — knowledge tags are always strings in our domain.
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
    session: Session, tags: Sequence[str]
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

    Returns:
        One :class:`KnowledgeStat` per requested tag, in the same
        order as ``tags``.
    """
    if not tags:
        return []

    full = {s.tag: s for s in compute_knowledge_stats(session)}
    return [
        full.get(
            tag,
            KnowledgeStat(tag=tag, total=0, passed=0, accuracy=0.0),
        )
        for tag in tags
    ]
