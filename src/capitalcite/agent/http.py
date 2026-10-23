"""httpx JSON client used by XBRL and FDIC tools. Tests pass MockTransport."""

from __future__ import annotations

import json
from typing import Protocol

import httpx


class Limiter(Protocol):
    async def acquire(self) -> None: ...


class BytesClient(Protocol):
    async def get_bytes(self, url: str) -> bytes:
        """HTTP 200 body. Any other status, including 301, raises ValueError."""


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
        """Return response.content for a 200.

        Acquire the limiter once when one is set. Map httpx.HTTPError to
        ValueError('request failed for {url}'). Map any other status to
        ValueError('HTTP {status} for {url}').
        """
        try:
            if self._limiter is not None:
                await self._limiter.acquire()
            # Leave follow_redirects unset. A 301 must stay an error so a
            # moved BankFind host cannot be swallowed as success.
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers={"User-Agent": self._user_agent})
        except httpx.HTTPError as exc:
            raise ValueError(f"request failed for {url}") from exc
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code} for {url}")
        return response.content

    async def get_json(self, url: str) -> object:
        body = await self.get_bytes(url)
        try:
            parsed: object = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("response was not JSON") from exc
        return parsed
