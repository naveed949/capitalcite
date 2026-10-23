"""FDIC BankFind institutions lookup. Tests inject a client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

BANKFIND_ORIGIN = "https://api.fdic.gov/banks"
_BANKFIND = f"{BANKFIND_ORIGIN}/institutions"


class JsonClient(Protocol):
    async def get_json(self, url: str) -> object: ...


class Institution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    cert: str
    active: bool


class FdicClient:
    def __init__(self, client: JsonClient) -> None:
        self._client = client

    async def lookup(self, query: str) -> str:
        needle = query.strip()
        if not needle:
            raise ValueError("fdic_bankfind query is empty")
        payload = await self._client.get_json(search_institutions_url(needle))
        _total, rows = institutions(payload)
        parts: list[str] = []
        for row in rows[:5]:
            name = row.name or needle
            parts.append(f"{name} CERT={row.cert}".strip())
        if not parts:
            raise ValueError(f"no FDIC institution matched {query!r}")
        return "; ".join(parts)


class FakeFdicClient:
    def __init__(self, records: Mapping[str, str] | None = None) -> None:
        self._records = dict(records or {})

    async def lookup(self, query: str) -> str:
        needle = query.strip().lower()
        for name, record in self._records.items():
            if needle in name.lower():
                return record
        raise ValueError(f"no FDIC institution matched {query!r}")


def search_institutions_url(query: str) -> str:
    return _institutions_url({"filters": f'NAME:"{query}"', "limit": "5", "format": "json"})


def lead_institutions_url(legal_name: str) -> str:
    if '"' in legal_name:
        raise ValueError("lead bank name cannot contain a double quote")
    filters = f'NAME:"{legal_name}" AND ACTIVE:1'
    return _institutions_url({"filters": filters, "limit": "10", "format": "json"})


def institutions(payload: object) -> tuple[int | None, tuple[Institution, ...]]:
    if not isinstance(payload, dict):
        raise ValueError("fdic_bankfind response was not an object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("fdic_bankfind response has no data list")
    total = _page_total(payload.get("meta"))
    rows: list[Institution] = []
    for row in data:
        body = _row_body(row)
        if body is None:
            continue
        rows.append(
            Institution(
                name=_text(body.get("NAME"), body.get("name")),
                cert=_cert_text(body.get("CERT"), body.get("cert")),
                active=_is_active(body.get("ACTIVE", body.get("active"))),
            )
        )
    return total, tuple(rows)


def _institutions_url(params: dict[str, str]) -> str:
    return f"{_BANKFIND}?" + urlencode(params)


def _page_total(meta: object) -> int | None:
    if not isinstance(meta, dict):
        return None
    total = meta.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


def _row_body(row: object) -> dict[str, object] | None:
    if not isinstance(row, dict):
        return None
    inner = row.get("data")
    if isinstance(inner, dict):
        return inner
    return row


def _text(primary: object, fallback: object) -> str:
    chosen = primary if _present(primary) else fallback
    if not _present(chosen):
        return ""
    return str(chosen).strip()


def _cert_text(primary: object, fallback: object) -> str:
    chosen = primary if _present(primary) else fallback
    if not _present(chosen):
        return ""
    if isinstance(chosen, bool):
        return str(chosen)
    if isinstance(chosen, int):
        return str(chosen)
    if isinstance(chosen, float) and chosen.is_integer():
        return str(int(chosen))
    return str(chosen).strip()


def _present(value: object) -> bool:
    return value is not None and value != ""


def _is_active(value: object) -> bool:
    return value in (1, "1", True)
