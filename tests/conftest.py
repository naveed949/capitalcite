"""Test process env. Loaded before test modules import the app."""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest

os.environ.setdefault("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# Lifespan calls configure(). The SDK exports OTLP, which the httpx block does not catch.
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
# A developer .env with NEO4J_URI must not open Bolt during pytest.
os.environ["NEO4J_URI"] = ""
os.environ["NEO4J_PASSWORD"] = ""
# Rich/Typer help wrapping follows COLUMNS. CI/non-TTY widths truncate
# "--tickers" to "--ticke…". Pin a wide dumb terminal for all tests.
os.environ["COLUMNS"] = "120"
os.environ["TERM"] = "dumb"
os.environ["NO_COLOR"] = "1"


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI must never open a real socket. Recorded fixtures use MockTransport."""

    def blocked_sync(self: object, request: object) -> None:
        raise RuntimeError(f"live network is forbidden in tests: {request}")

    async def blocked_async(self: object, request: object) -> None:
        raise RuntimeError(f"live network is forbidden in tests: {request}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


@pytest.fixture(autouse=True)
def reset_trace_state() -> Iterator[None]:
    from capitalcite.trace import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()
