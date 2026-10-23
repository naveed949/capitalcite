from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from capitalcite.api.app import create_app
from capitalcite.api.ask_limit import AskLimiter
from capitalcite.config import get_settings

_BODY = {"question": "What is the tier 1 capital buffer?", "model": "fake"}


async def test_sliding_window_blocks_then_expires() -> None:
    limiter = AskLimiter(limit=2, window_s=10)
    first = await limiter.hit("203.0.113.4", 100.0)
    second = await limiter.hit("203.0.113.4", 101.0)
    blocked = await limiter.hit("203.0.113.4", 102.0)
    other = await limiter.hit("203.0.113.9", 102.0)
    later = await limiter.hit("203.0.113.4", 110.0)

    assert first.allowed is True
    assert second.allowed is True
    assert blocked.allowed is False
    assert blocked.retry_after_s == 8
    assert other.allowed is True
    assert later.allowed is True


async def test_zero_limit_does_not_count() -> None:
    limiter = AskLimiter(limit=0, window_s=10)
    for _ in range(5):
        decision = await limiter.hit("203.0.113.4", 1.0)
        assert decision.allowed is True
        assert decision.retry_after_s == 0


def test_ask_and_stream_return_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, tmp_path, limit="2")
    with TestClient(create_app()) as client:
        health_codes = [client.get("/health").status_code for _ in range(4)]
        ask_codes = [client.post("/ask", json=_BODY).status_code for _ in range(3)]
        stream = client.post(
            "/ask/stream",
            json=_BODY,
            headers={"X-Forwarded-For": "198.51.100.8"},
        )

    assert health_codes == [200, 200, 200, 200]
    assert ask_codes[0] != 429
    assert ask_codes[1] != 429
    assert ask_codes[2] == 429
    assert stream.status_code == 429
    assert "text/event-stream" not in stream.headers["content-type"]
    assert isinstance(stream.json()["detail"], str) and stream.json()["detail"]
    retry_after = int(stream.headers["retry-after"])
    assert retry_after >= 1


def test_forwarded_for_does_not_reset_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(monkeypatch, tmp_path, limit="1")
    with TestClient(create_app()) as client:
        first = client.post("/ask", json=_BODY, headers={"X-Forwarded-For": "198.51.100.1"})
        second = client.post("/ask", json=_BODY, headers={"X-Forwarded-For": "198.51.100.2"})

    assert first.status_code != 429
    assert second.status_code == 429


def test_unlimited_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, tmp_path, limit=None)
    with TestClient(create_app()) as client:
        codes = [client.post("/ask", json=_BODY).status_code for _ in range(4)]

    assert 429 not in codes


def _prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, limit: str | None) -> None:
    store = tmp_path / "qdrant"
    store.mkdir()
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("QDRANT_PATH", str(store))
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ASK_MODEL", "fake")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    if limit is None:
        monkeypatch.delenv("ASK_RATE_LIMIT", raising=False)
    else:
        monkeypatch.setenv("ASK_RATE_LIMIT", limit)
    get_settings.cache_clear()
