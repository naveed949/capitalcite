"""httpx JSON client used by XBRL and FDIC tools. Tests pass MockTransport."""

from __future__ import annotations

import json

import httpx


class HttpxJsonClient:
    def __init__(
        self,
        *,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._user_agent = user_agent
        self._transport = transport
        self._timeout = timeout

    async def get_json(self, url: str) -> object:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(url, headers={"User-Agent": self._user_agent})
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code} for {url}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ValueError("response was not JSON") from exc
