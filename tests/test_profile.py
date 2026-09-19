"""Tests for the Profile API (P1-20).

Exercises:
- GET /api/v1/profile/knowledge-stats  — all tags with submissions
- GET /api/v1/profile/knowledge-stats?tags=...  — filtered subset + placeholders
- GET /api/v1/profile/radar  — ECharts-ready payload (indicators + values)
- Empty database → empty payloads (no crash)
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import mistake_book as mb_module
from app.api import profile as profile_module
from app.main import app
from app.models.base import Base
from app.models.mistake_book import (
    MistakeBookEntry,  # noqa: F401 — register on Base.metadata
)
from app.models.problem import Problem
from app.models.submission import Submission
from conftest import TEST_SIDECAR_HEADERS


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    # Seed two problems + submissions:
    # dp:     2 AC, 1 WA  → 0.667
    # 贪心:   0 AC, 2 WA  → 0.0
    p1 = Problem(id=1, title="背包", knowledge_tags=["dp", "贪心"])
    p2 = Problem(id=2, title="最长上升", knowledge_tags=["dp"])
    db_session.add(p1)
    db_session.add(p2)
    for _ in range(2):
        db_session.add(Submission(problem_id=1, status="AC"))
    db_session.add(Submission(problem_id=1, status="WA"))
    db_session.add(Submission(problem_id=2, status="AC"))
    db_session.add(Submission(problem_id=2, status="AC"))
    db_session.add(Submission(problem_id=2, status="WA"))
    db_session.commit()

    # Patch BOTH modules — mistake_book.get_session is the source, and
    # profile.py imports it by reference, so we patch at the profile
    # module's binding too.
    real_mb_get_session = mb_module.get_session
    real_profile_get_session = profile_module.get_session

    def fake_get_session() -> Session:
        return db_session

    mb_module.get_session = fake_get_session  # type: ignore[assignment]
    profile_module.get_session = fake_get_session  # type: ignore[assignment]

    try:
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
            yield c
    finally:
        mb_module.get_session = real_mb_get_session  # type: ignore[assignment]
        profile_module.get_session = real_profile_get_session  # type: ignore[assignment]


# --- knowledge-stats ---------------------------------------------------


def test_knowledge_stats_all(client: TestClient) -> None:
    """GET /knowledge-stats returns all tags with submissions."""
    resp = client.get("/api/v1/profile/knowledge-stats")
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    by_tag = {s["tag"]: s for s in stats}
    # dp appears on both problems: 2+2 AC, 1+1 WA = 4 AC / 6 total
    assert by_tag["dp"]["total"] == 6
    assert by_tag["dp"]["passed"] == 4
    assert by_tag["dp"]["accuracy"] == round(4 / 6, 4)
    # 贪心 only on p1: 2 AC, 1 WA
    assert by_tag["贪心"]["total"] == 3
    assert by_tag["贪心"]["passed"] == 2
    assert by_tag["贪心"]["accuracy"] == round(2 / 3, 4)


def test_knowledge_stats_filtered_with_placeholder(client: TestClient) -> None:
    """?tags= returns placeholders for tags with no submissions."""
    resp = client.get(
        "/api/v1/profile/knowledge-stats?tags=dp&tags=未学过"
    )
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    by_tag = {s["tag"]: s for s in stats}
    assert set(by_tag) == {"dp", "未学过"}
    assert by_tag["dp"]["total"] == 6
    assert by_tag["未学过"]["total"] == 0
    assert by_tag["未学过"]["accuracy"] == 0.0


def test_knowledge_stats_empty_db(db_session: Session) -> None:
    """No submissions → empty stats list (no crash)."""
    real_mb = mb_module.get_session
    real_p = profile_module.get_session

    def fake() -> Session:
        return db_session

    mb_module.get_session = fake  # type: ignore[assignment]
    profile_module.get_session = fake  # type: ignore[assignment]
    try:
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
            resp = c.get("/api/v1/profile/knowledge-stats")
            assert resp.status_code == 200
            assert resp.json()["stats"] == []
    finally:
        mb_module.get_session = real_mb  # type: ignore[assignment]
        profile_module.get_session = real_p  # type: ignore[assignment]


# --- radar -------------------------------------------------------------


def test_radar_returns_echarts_payload(client: TestClient) -> None:
    """GET /radar returns indicators + values + tags in parallel order."""
    resp = client.get("/api/v1/profile/radar")
    assert resp.status_code == 200
    body = resp.json()
    # Three parallel arrays of equal length.
    assert len(body["indicators"]) == len(body["values"]) == len(body["tags"])
    # Indicators have name + max=1.0.
    for ind in body["indicators"]:
        assert "name" in ind
        assert ind["max"] == 1.0
    # dp accuracy = 4/6
    idx_dp = body["tags"].index("dp")
    assert body["values"][idx_dp] == round(4 / 6, 4)


def test_radar_filtered(client: TestClient) -> None:
    """?tags= filters the radar to the requested tags."""
    resp = client.get("/api/v1/profile/radar?tags=dp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tags"] == ["dp"]
    assert len(body["indicators"]) == 1
    assert body["values"][0] == round(4 / 6, 4)
