from __future__ import annotations

from fastapi.testclient import TestClient

from capitalcite.api import app


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "capitalcite"
    assert "version" in body
