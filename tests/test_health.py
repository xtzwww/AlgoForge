"""Tests for the /health endpoint (P1-1)."""

from fastapi.testclient import TestClient

from app.code_stamp import STAMP_LENGTH
from app.main import app
from conftest import TEST_SIDECAR_HEADERS

client = TestClient(app, headers=TEST_SIDECAR_HEADERS)


def test_health_returns_200_and_ok() -> None:
    """GET /health should return 200 with {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"

    # `code_stamp` is additive: the launcher hashes `sidecar/app` and
    # compares, so that a backend started before the current edit is
    # restarted instead of silently answering 404 for a route that exists
    # on disk (see app/code_stamp.py). Asserted as a shape, not a value —
    # the digest changes with every edit to `app/`.
    assert isinstance(body["code_stamp"], str)
    assert len(body["code_stamp"]) == STAMP_LENGTH
