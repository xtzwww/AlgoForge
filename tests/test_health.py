"""Tests for the /health endpoint (P1-1)."""

from fastapi.testclient import TestClient

from app.main import app
from conftest import TEST_SIDECAR_HEADERS

client = TestClient(app, headers=TEST_SIDECAR_HEADERS)


def test_health_returns_200_and_ok() -> None:
    """GET /health should return 200 with {\"status\": \"ok\"}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
