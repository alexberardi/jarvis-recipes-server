"""Tests for GET /health.

/health is the liveness probe used by the `./jarvis` tier gate, by
jarvis-config-service's aggregate health view and by the prod compose
healthcheck. It had no test at all, so a global auth dependency (or a rename)
would have 401'd/404'd all three with nothing catching it. These tests pin both
the contract and the fact that it takes no credentials.
"""

import pytest
from fastapi.testclient import TestClient

from jarvis_recipes.app.main import create_app


@pytest.fixture
def unauthenticated_client() -> TestClient:
    """A client over a plain app — no dependency overrides, no credentials."""
    return TestClient(create_app())


def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_requires_no_auth(unauthenticated_client):
    resp = unauthenticated_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_ignores_a_bogus_bearer_token(unauthenticated_client):
    """A caller with junk credentials still gets a liveness answer, not a 401."""
    resp = unauthenticated_client.get(
        "/health", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
