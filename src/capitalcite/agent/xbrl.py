"""SEC companyfacts lookup. Tests inject a client. No network in CI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from capitalcite.holdings import HoldingCompany, holding_company


class JsonClient(Protocol):
    async def get_json(self, url: str) -> object: ...


def companyfacts_url(company: HoldingCompany) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company.cik}.json"


class XbrlClient:
    """Reads companyfacts JSON through an injected client."""

    def __init__(self, client: JsonClient) -> None:
        self._client = client

    async def fact(self, ticker: str, concept: str, period: str | None) -> str:
        symbol = ticker.strip().upper()
        try:
            company = holding_company(symbol)
        except KeyError:
            raise ValueError(f"unknown ticker for xbrl_fact: {ticker}") from None
        payload = await self._client.get_json(companyfacts_url(company))
        value = _pick_fact(payload, concept.strip(), period)
        if value is None:
            raise ValueError(f"no xbrl fact for {symbol} {concept}")
        return value


class FakeXbrlClient:
    """Returns canned facts. No HTTP."""

    def __init__(self, facts: Mapping[tuple[str, str], str] | None = None) -> None:
        self._facts = dict(facts or {})

    async def fact(self, ticker: str, concept: str, period: str | None) -> str:
        key = (ticker.strip().upper(), concept.strip())
        if key not in self._facts:
            raise ValueError(f"no xbrl fact for {key[0]} {key[1]}")
        _ = period
        return self._facts[key]


def _pick_fact(payload: object, concept: str, period: str | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        return None
    for taxonomy in facts.values():
        if not isinstance(taxonomy, dict):
            continue
        entry = taxonomy.get(concept)
        if not isinstance(entry, dict):
            continue
        units = entry.get("units")
        if not isinstance(units, dict):
            continue
        for series in units.values():
            if not isinstance(series, list):
                continue
            picked = _pick_observation(series, period)
            if picked is not None:
                return picked
    return None


def _pick_observation(series: list[object], period: str | None) -> str | None:
    wanted_year: str | None = None
    if period is not None and len(period) >= 4 and period[:4].isdigit():
        wanted_year = period[:4]
    last: str | None = None
    for row in series:
        if not isinstance(row, dict):
            continue
        val = row.get("val")
        if val is None:
            continue
        rendered = str(val)
        end = str(row.get("end") or "")
        fy = str(row.get("fy") or "")
        last = rendered
        if wanted_year and (end.startswith(wanted_year) or fy == wanted_year):
            return rendered
    if wanted_year is None:
        return last
    return None
