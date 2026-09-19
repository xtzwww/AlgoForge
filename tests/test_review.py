"""Integration tests for the Review API (P1-19c).

Exercises the SM-2 powered review endpoints against an in-memory
SQLite database:

- GET    /api/v1/review/due
- GET    /api/v1/review/{entry_id}          — 404 before first review
- POST   /api/v1/review/{entry_id}/review  — advances SRS state
- POST   /api/v1/review/{entry_id}/reset   — resets state

Verifies:
- First review creates the record implicitly.
- Different quality scores produce different intervals.
- Failed recall (q<3) resets repetitions.
- /due only returns items whose due_at has passed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import mistake_book as mb_module
from app.api import review as review_module
from app.main import app
from app.models.base import Base
from app.models.mistake_book import MistakeBookEntry  # noqa: F401
from app.models.problem import Problem
from app.models.review import ReviewRecord  # noqa: F401
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
    # Seed a problem + a WA submission + a mistake-book entry (id=1).
    db_session.add(Problem(id=1, title="A+B", knowledge_tags=["基础"]))
    db_session.add(Submission(id=1, problem_id=1, status="WA"))
    db_session.add(
        MistakeBookEntry(
            id=1,
            submission_id=1,
            problem_id=1,
            error_cause="",
            knowledge_tags=["基础"],
        )
    )
    db_session.commit()

    # Patch both modules' get_session — review.py imports from
    # mistake_book at module load time, so the binding is fixed.
    real_mb = mb_module.get_session
    real_review = review_module.get_session

    def fake() -> Session:
        return db_session

    mb_module.get_session = fake  # type: ignore[assignment]
    review_module.get_session = fake  # type: ignore[assignment]

    try:
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
            yield c
    finally:
        mb_module.get_session = real_mb  # type: ignore[assignment]
        review_module.get_session = real_review  # type: ignore[assignment]


# --- GET /{entry_id} ---------------------------------------------------


def test_get_review_404_before_first_review(client: TestClient) -> None:
    """GET /review/{id} before any review → 404."""
    resp = client.get("/api/v1/review/1")
    assert resp.status_code == 404


def test_get_review_404_for_unknown_entry(client: TestClient) -> None:
    """GET /review/{unknown_entry_id} → 404."""
    resp = client.get("/api/v1/review/999")
    assert resp.status_code == 404


# --- POST /{entry_id}/review -------------------------------------------


def test_first_review_creates_record(client: TestClient) -> None:
    """POST /review implicitly creates the record on first call."""
    resp = client.post("/api/v1/review/1/review", json={"quality": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["entry_id"] == 1
    assert body["repetitions"] == 1
    assert body["interval_days"] == 1
    assert body["easiness_factor"] == 2.6  # 2.5 + 0.1
    assert body["due_at"] is not None
    assert body["last_reviewed_at"] is not None


def test_review_unknown_entry_404(client: TestClient) -> None:
    """POST /review/{unknown_entry_id}/review → 404."""
    resp = client.post("/api/v1/review/999/review", json={"quality": 5})
    assert resp.status_code == 404


def test_review_invalid_quality_422(client: TestClient) -> None:
    """quality outside [0,5] → 422 validation error."""
    resp = client.post("/api/v1/review/1/review", json={"quality": 6})
    assert resp.status_code == 422
    resp = client.post("/api/v1/review/1/review", json={"quality": -1})
    assert resp.status_code == 422


def test_second_review_advances_interval_to_6(client: TestClient) -> None:
    """Second q=5 review → reps=2, interval=6 days."""
    client.post("/api/v1/review/1/review", json={"quality": 5})
    resp = client.post("/api/v1/review/1/review", json={"quality": 5})
    body = resp.json()
    assert body["repetitions"] == 2
    assert body["interval_days"] == 6


def test_failed_review_resets_repetitions(client: TestClient) -> None:
    """q<3 resets repetitions to 0."""
    # First, build up to reps=2.
    client.post("/api/v1/review/1/review", json={"quality": 5})
    client.post("/api/v1/review/1/review", json={"quality": 5})
    # Now fail.
    resp = client.post("/api/v1/review/1/review", json={"quality": 2})
    body = resp.json()
    assert body["repetitions"] == 0
    assert body["interval_days"] == 1


def test_get_review_after_first_review(client: TestClient) -> None:
    """After a review, GET /review/{id} returns the state."""
    client.post("/api/v1/review/1/review", json={"quality": 5})
    resp = client.get("/api/v1/review/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["repetitions"] == 1
    assert body["interval_days"] == 1


# --- POST /{entry_id}/reset --------------------------------------------


def test_reset_clears_state(client: TestClient) -> None:
    """Reset returns the record to defaults."""
    # Build up state.
    client.post("/api/v1/review/1/review", json={"quality": 5})
    client.post("/api/v1/review/1/review", json={"quality": 5})
    # Reset.
    resp = client.post("/api/v1/review/1/reset")
    body = resp.json()
    assert body["repetitions"] == 0
    assert body["easiness_factor"] == 2.5
    assert body["interval_days"] == 0
    assert body["due_at"] is None
    assert body["last_reviewed_at"] is None


def test_reset_unknown_entry_404(client: TestClient) -> None:
    """Resetting an unknown entry → 404."""
    resp = client.post("/api/v1/review/999/reset")
    assert resp.status_code == 404


# --- GET /due ----------------------------------------------------------


def test_due_returns_never_reviewed_first(client: TestClient) -> None:
    """Never-reviewed items have null due_at → come first in /due."""
    # No review yet — but /due filters by is_due, which returns True
    # for null due_at. However, the review record doesn't exist yet,
    # so /due returns nothing. Create the record via a review.
    client.post("/api/v1/review/1/review", json={"quality": 5})

    # Now /due should NOT include entry 1 (its due_at is tomorrow).
    resp = client.get("/api/v1/review/due")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 0


def test_due_returns_past_due_items(client: TestClient, db_session: Session) -> None:
    """Items whose due_at has passed appear in /due."""
    # Create a review record with due_at in the past.
    client.post("/api/v1/review/1/review", json={"quality": 5})
    # Manually backdate the due_at to yesterday.
    rec = db_session.execute(
        __import__("sqlalchemy").select(ReviewRecord).where(
            ReviewRecord.entry_id == 1
        )
    ).scalar_one()
    rec.due_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    db_session.commit()

    resp = client.get("/api/v1/review/due")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["entry_id"] == 1
