"""Tests for the MistakeBook model + API (P1-18).

Exercises both the ORM row behavior and the CRUD endpoints. Uses an
isolated in-memory SQLite database injected through FastAPI's
dependency system so the test does not touch the dev SQLite file.

Endpoints covered:
- POST   /api/v1/mistake-book              — create from submission
- GET    /api/v1/mistake-book               — list with filters
- GET    /api/v1/mistake-book/{id}          — fetch one
- PATCH  /api/v1/mistake-book/{id}          — update fields
- DELETE /api/v1/mistake-book/{id}          — remove

Edge cases:
- 404 on unknown submission / entry
- 400 when adding an AC submission
- knowledge_tags snapshot from problem at creation time
- reviewed False→True bumps review_count + last_reviewed_at
- reviewed True→False does NOT decrement review_count
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import mistake_book as mb_module
from app.main import app
from app.models.base import Base
from app.models.mistake_book import (
    MistakeBookEntry,  # noqa: F401 — register table on Base.metadata
)
from app.models.problem import Problem
from app.models.submission import Submission
from conftest import TEST_SIDECAR_HEADERS


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Fresh in-memory SQLite session with schema created.

    Uses StaticPool + a single connection so every session sees the
    same in-memory database (the default pool would create a fresh
    empty in-memory db per connection, which breaks the test).
    """
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
    """FastAPI client with the mistake-book session overridden.

    Routes call ``get_session()`` directly; we patch that function so
    every request reuses the in-memory test session.
    """
    # Pre-seed a problem + a WA submission so create_entry has something
    # to reference. problem_id=1, submission_id=1.
    problem = Problem(id=1, title="A+B", knowledge_tags=["基础", "模拟"])
    db_session.add(problem)
    db_session.add(Submission(id=1, problem_id=1, status="WA"))
    db_session.add(Submission(id=2, problem_id=1, status="AC"))
    db_session.commit()

    # Monkeypatch the session provider used inside the routes.
    real_get_session = mb_module.get_session

    def fake_get_session() -> Session:
        return db_session

    mb_module.get_session = fake_get_session  # type: ignore[assignment]
    # `_ensure_tables` is called by the real get_session; with the
    # monkeypatch in place it never runs, but silence linters by leaving
    # the override simple.
    try:
        with TestClient(app, headers=TEST_SIDECAR_HEADERS) as c:
            yield c
    finally:
        mb_module.get_session = real_get_session  # type: ignore[assignment]


# --- Create ------------------------------------------------------------


def test_create_entry_from_wa_submission(client: TestClient) -> None:
    """POST creates an entry, pulling problem_id + tags automatically."""
    resp = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "忘了处理负数"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == 1
    assert body["submission_id"] == 1
    assert body["problem_id"] == 1
    assert body["error_cause"] == "忘了处理负数"
    assert body["knowledge_tags"] == ["基础", "模拟"]
    assert body["reviewed"] is False
    assert body["review_count"] == 0
    assert body["last_reviewed_at"] is None
    assert body["created_at"] is not None


def test_create_entry_rejects_ac_submission(client: TestClient) -> None:
    """Adding an AC submission is a client bug → 400."""
    resp = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 2, "error_cause": ""},
    )
    assert resp.status_code == 400
    assert "AC" in resp.json()["detail"]


def test_create_entry_404_on_unknown_submission(client: TestClient) -> None:
    """Unknown submission_id → 404."""
    resp = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 999, "error_cause": ""},
    )
    assert resp.status_code == 404


# --- List + filter -----------------------------------------------------


def test_list_entries_unfiltered(client: TestClient) -> None:
    """GET returns all entries.

    Note: We don't assert ordering on created_at because SQLite's
    ``func.now()`` has second-level precision and two entries created
    in the same request batch share the same timestamp. The API
    sorts by created_at desc; this test only verifies that all
    entries come back, not the order.
    """
    client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "first"},
    )
    client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "second"},
    )

    resp = client.get("/api/v1/mistake-book")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    causes = {entry["error_cause"] for entry in body}
    assert causes == {"first", "second"}


def test_list_entries_filter_by_reviewed(client: TestClient) -> None:
    """GET ?reviewed=true returns only reviewed entries."""
    create_resp = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "未复习"},
    )
    entry_id = create_resp.json()["id"]

    # Mark it reviewed.
    client.patch(f"/api/v1/mistake-book/{entry_id}", json={"reviewed": True})

    reviewed = client.get("/api/v1/mistake-book?reviewed=true").json()
    not_reviewed = client.get("/api/v1/mistake-book?reviewed=false").json()
    assert len(reviewed) == 1
    assert reviewed[0]["id"] == entry_id
    assert len(not_reviewed) == 0


def test_list_entries_filter_by_problem_id(client: TestClient) -> None:
    """GET ?problem_id=1 returns only entries on problem 1."""
    client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "p1 entry"},
    )
    resp = client.get("/api/v1/mistake-book?problem_id=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["problem_id"] == 1


# --- Get one -----------------------------------------------------------


def test_get_entry_by_id(client: TestClient) -> None:
    """GET /{id} returns the entry."""
    create_resp = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "找一下"},
    )
    entry_id = create_resp.json()["id"]

    resp = client.get(f"/api/v1/mistake-book/{entry_id}")
    assert resp.status_code == 200
    assert resp.json()["error_cause"] == "找一下"


def test_get_entry_404(client: TestClient) -> None:
    """GET /{unknown_id} → 404."""
    resp = client.get("/api/v1/mistake-book/999")
    assert resp.status_code == 404


# --- Patch -------------------------------------------------------------


def test_patch_error_cause(client: TestClient) -> None:
    """PATCH can update error_cause without touching reviewed."""
    entry_id = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "原"},
    ).json()["id"]

    resp = client.patch(
        f"/api/v1/mistake-book/{entry_id}",
        json={"error_cause": "改了"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error_cause"] == "改了"
    assert body["reviewed"] is False
    assert body["review_count"] == 0


def test_patch_reviewed_true_increments_count(client: TestClient) -> None:
    """reviewed False→True bumps review_count + stamps last_reviewed_at."""
    entry_id = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": ""},
    ).json()["id"]

    resp = client.patch(
        f"/api/v1/mistake-book/{entry_id}",
        json={"reviewed": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewed"] is True
    assert body["review_count"] == 1
    assert body["last_reviewed_at"] is not None


def test_patch_reviewed_false_does_not_decrement(client: TestClient) -> None:
    """Flipping reviewed back to False keeps review_count (lifetime total)."""
    entry_id = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": ""},
    ).json()["id"]

    # review once
    client.patch(f"/api/v1/mistake-book/{entry_id}", json={"reviewed": True})
    # flip back
    resp = client.patch(f"/api/v1/mistake-book/{entry_id}", json={"reviewed": False})
    body = resp.json()
    assert body["reviewed"] is False
    assert body["review_count"] == 1  # not decremented


def test_patch_404(client: TestClient) -> None:
    """PATCH /{unknown_id} → 404."""
    resp = client.patch("/api/v1/mistake-book/999", json={"reviewed": True})
    assert resp.status_code == 404


# --- Delete ------------------------------------------------------------


def test_delete_entry(client: TestClient) -> None:
    """DELETE removes the entry; subsequent GET → 404."""
    entry_id = client.post(
        "/api/v1/mistake-book",
        json={"submission_id": 1, "error_cause": "删我"},
    ).json()["id"]

    resp = client.delete(f"/api/v1/mistake-book/{entry_id}")
    assert resp.status_code == 204

    # Subsequent get → 404
    assert client.get(f"/api/v1/mistake-book/{entry_id}").status_code == 404


def test_delete_404(client: TestClient) -> None:
    """DELETE /{unknown_id} → 404."""
    resp = client.delete("/api/v1/mistake-book/999")
    assert resp.status_code == 404
