"""Profile API endpoints (P1-20).

Returns knowledge-point accuracy aggregates for the radar chart and
trend views on the Profile page (P1-22).

- GET /api/v1/profile/knowledge-stats
    Per-tag {tag, total, passed, accuracy} across all submissions.
- GET /api/v1/profile/radar
    ECharts-ready radar payload: indicators + values, so the frontend
    can feed it directly into a ``<v-chart>`` instance.

Both endpoints reuse P1-17's ``compute_knowledge_stats`` aggregator.
The session handling mirrors P1-18's mistake-book API (sync, SQLite
fallback in dev). The async swap is deferred to a dedicated refactor
slice — see P1-18's "未做的事" notes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.db import get_session
from app.models.knowledge_stats import KnowledgeStat, compute_knowledge_stats_for

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


# --- Response schemas --------------------------------------------------


class KnowledgeStatOut(BaseModel):
    tag: str
    total: int
    passed: int
    accuracy: float


class KnowledgeStatsResponse(BaseModel):
    stats: list[KnowledgeStatOut]


class RadarIndicator(BaseModel):
    name: str
    max: float = 1.0


class RadarResponse(BaseModel):
    indicators: list[RadarIndicator]
    values: list[float]
    tags: list[str]


def _stat_to_out(stat: KnowledgeStat) -> KnowledgeStatOut:
    return KnowledgeStatOut(
        tag=stat.tag,
        total=stat.total,
        passed=stat.passed,
        accuracy=stat.accuracy,
    )


# --- Routes ------------------------------------------------------------


@router.get("/knowledge-stats", response_model=KnowledgeStatsResponse)
def get_knowledge_stats(
    tags: Optional[list[str]] = Query(default=None),
) -> KnowledgeStatsResponse:
    """Return per-tag accuracy stats.

    Args:
        tags: Optional filter — only return these tags. Tags with no
            submissions are still returned with total=0, accuracy=0.0
            so the caller (radar chart, etc.) sees a stable axis set.
            If omitted, returns all tags that have at least one
            submission (compute_knowledge_stats's default behavior).
    """
    with get_session() as session:
        if tags:
            stats = compute_knowledge_stats_for(session, tags)
        else:
            # compute_knowledge_stats returns all tags with submissions.
            # We can't use the `_for` variant because it requires the
            # tag list; we want "all".
            from app.models.knowledge_stats import compute_knowledge_stats

            stats = compute_knowledge_stats(session)
        return KnowledgeStatsResponse(stats=[_stat_to_out(s) for s in stats])


@router.get("/radar", response_model=RadarResponse)
def get_radar(
    tags: Optional[list[str]] = Query(default=None),
) -> RadarResponse:
    """Return ECharts-ready radar data.

    The radar chart needs two parallel arrays:
    - ``indicators``: [{name: "动态规划", max: 1.0}, ...]
    - ``values``: [0.65, 0.80, ...]  (one accuracy per indicator, in order)

    We also return ``tags`` so the caller can build tooltips/legends
    without re-parsing indicators.

    Args:
        tags: Optional filter — same semantics as
            ``GET /knowledge-stats``. When omitted, returns all tags
            that have submissions.
    """
    with get_session() as session:
        if tags:
            stats = compute_knowledge_stats_for(session, tags)
        else:
            from app.models.knowledge_stats import compute_knowledge_stats

            stats = compute_knowledge_stats(session)

        indicators = [RadarIndicator(name=s.tag, max=1.0) for s in stats]
        values = [s.accuracy for s in stats]
        tags_out = [s.tag for s in stats]
        return RadarResponse(indicators=indicators, values=values, tags=tags_out)
