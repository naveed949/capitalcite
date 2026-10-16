"""FDIC BankFind institutions lookup. Tests inject a client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlencode

_BANKFIND = "https://banks.data.fdic.gov/api/institutions"


class JsonClient(Protocol):
    async def get_json(self, url: str) -> object: ...


class FdicClient:
    def __init__(self, client: JsonClient) -> None:
        self._client = client

    async def lookup(self, query: str) -> str:
        needle = query.strip()
        if not needle:
            raise ValueError("fdic_bankfind query is empty")
        url = f"{_BANKFIND}?" + urlencode(
            {"filters": f'NAME:"{needle}"', "limit": "5", "format": "json"}
        )
        payload = await self._client.get_json(url)
        return _summarize(payload, needle)


class FakeFdicClient:
    def __init__(self, records: Mapping[str, str] | None = None) -> None:
        self._records = dict(records or {})

    async def lookup(self, query: str) -> str:
        needle = query.strip().lower()
        for name, record in self._records.items():
            if needle in name.lower():
                return record
        raise ValueError(f"no FDIC institution matched {query!r}")


def _summarize(payload: object, query: str) -> str:
    if not isinstance(payload, dict):
        raise ValueError("fdic_bankfind response was not an object")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError(f"no FDIC institution matched {query!r}")
    parts: list[str] = []
    for row in data[:5]:
        if not isinstance(row, dict):
            continue
        body = row.get("data") if isinstance(row.get("data"), dict) else row
        if not isinstance(body, dict):
            continue
        name = body.get("NAME") or body.get("name") or query
        cert = body.get("CERT") or body.get("cert") or ""
        parts.append(f"{name} CERT={cert}".strip())
    if not parts:
        raise ValueError(f"no FDIC institution matched {query!r}")
    return "; ".join(parts)
