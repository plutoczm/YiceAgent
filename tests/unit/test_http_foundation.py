"""HTTP boundaries only; no live model calls."""

from fastapi.testclient import TestClient

from workflowops.app import create_app


def test_health_explicitly_reports_unverified_live():
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["live_verified"] is False
        assert client.get("/api/v2/models").json()["readiness"] == "NOT_RUN"


def test_host_and_origin_boundaries():
    with TestClient(create_app()) as client:
        assert client.get("/health", headers={"host": "evil.example"}).status_code == 403
        assert client.get("/health", headers={"origin": "https://evil.example"}).status_code == 403
        assert client.get("/health", headers={"origin": "http://127.0.0.1:8020"}).status_code == 200
