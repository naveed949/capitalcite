"""httpx JSON client used by XBRL and FDIC tools. Tests pass MockTransport."""

from __future__ import annotations

import json
from typing import Protocol

import httpx


class Limiter(Protocol):
    async def acquire(self) -> None: ...


class HttpClientError(ValueError):
    pass


class HttpTransportError(HttpClientError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"request failed for {url}")


class HttpStatusError(HttpClientError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")


class BytesClient(Protocol):
    async def get_bytes(self, url: str) -> bytes:
        """HTTP 200 body. Non-200, including 301, raises HttpStatusError."""


class HttpxJsonClient:
    def __init__(
        self,
        *,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        limiter: Limiter | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._transport = transport
        self._timeout = timeout
        self._limiter = limiter

    async def get_bytes(self, url: str) -> bytes:
        try:
            if self._limiter is not None:
                await self._limiter.acquire()
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.get(url, headers={"User-Agent": self._user_agent})
        except httpx.HTTPError as exc:
            raise HttpTransportError(url) from exc
        if response.status_code != 200:
            raise HttpStatusError(response.status_code, url)
        return response.content

    async def get_json(self, url: str) -> object:
        body = await self.get_bytes(url)
        try:
            parsed: object = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("response was not JSON") from exc
        return parsed
