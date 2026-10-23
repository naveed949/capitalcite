from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from capitalcite.api.app import create_app
from capitalcite.config import get_settings


def test_default_origin_has_no_cors_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_is_echoed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("CORS_ORIGINS", " https://example.com , https://other.example ")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        allowed = client.get("/health", headers={"Origin": "https://example.com"})
        denied = client.get("/health", headers={"Origin": "https://denied.example"})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://example.com"
    assert denied.status_code == 200
    assert "access-control-allow-origin" not in denied.headers


def test_blank_cors_origins_add_no_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("CORS_ORIGINS", " , ")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def _prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = tmp_path / "qdrant"
    store.mkdir()
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("QDRANT_PATH", str(store))
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ASK_MODEL", "fake")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    get_settings.cache_clear()
