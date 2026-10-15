"""Test process env. Loaded before test modules import the app."""

from __future__ import annotations

import os

import httpx
import pytest

os.environ.setdefault("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI must never open a real socket. Recorded fixtures use MockTransport."""

    def blocked_sync(self: object, request: object) -> None:
        raise RuntimeError(f"live network is forbidden in tests: {request}")

    async def blocked_async(self: object, request: object) -> None:
        raise RuntimeError(f"live network is forbidden in tests: {request}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
