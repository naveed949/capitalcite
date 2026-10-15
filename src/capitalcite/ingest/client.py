"""Async EDGAR HTTP client: tickers, submissions, primary documents."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Self

import httpx

from capitalcite.ingest.models import Company, Filing
from capitalcite.ingest.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_FILE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{document}"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 5
INITIAL_BACKOFF_S = 0.5
MAX_BACKOFF_S = 8.0
MAX_RETRY_AFTER_S = 30.0


class EdgarError(Exception):
    """Base error for EDGAR ingest."""


class EdgarAccessError(EdgarError):
    """SEC rejected the request (usually 403: missing or blocked User-Agent)."""


class EdgarUnavailableError(EdgarError):
    """Retries exhausted on 429/5xx or transport errors."""


class EdgarProtocolError(EdgarError):
    """A JSON payload did not match the documented submissions shape."""


class UnknownTickerError(EdgarError):
    """One or more requested tickers were missing from company_tickers.json."""

    def __init__(self, tickers: Sequence[str]) -> None:
        self.tickers = tuple(tickers)
        super().__init__(
            "unknown ticker(s): " + ", ".join(self.tickers) + " (not in company_tickers.json)"
        )


def archive_url(cik: str, accession: str, primary_document: str) -> str:
    """Build the EDGAR archives URL for a primary document."""
    document = primary_document.rsplit("/", 1)[-1]
    return ARCHIVE_URL.format(
        cik_int=int(cik),
        accession_nodash=accession.replace("-", ""),
        document=document,
    )


class EdgarClient:
    """Rate-limited httpx client for data.sec.gov and www.sec.gov."""

    def __init__(
        self,
        user_agent: str,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        limiter: RateLimiter | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("EDGAR User-Agent must include a contact email")
        self._user_agent = user_agent
        self._owns_client = client is None
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        kwargs: dict[str, object] = {
            "headers": headers,
            "follow_redirects": True,
            "timeout": httpx.Timeout(60.0),
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = client or httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
        if client is not None and "user-agent" not in {k.lower() for k in client.headers}:
            self._client.headers["User-Agent"] = user_agent
        self._limiter = limiter or RateLimiter()
        self._sleep = sleeper or asyncio.sleep
        self._max_attempts = max_attempts

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def lookup_companies(self, tickers: Sequence[str]) -> dict[str, Company]:
        """Resolve tickers via company_tickers.json. Unknown tickers raise."""
        wanted = [t.upper() for t in tickers]
        payload = await self._get_json(TICKERS_URL)
        if not isinstance(payload, dict):
            raise EdgarProtocolError("company_tickers.json is not an object")
        by_ticker: dict[str, Company] = {}
        for raw in payload.values():
            if not isinstance(raw, dict):
                continue
            ticker = str(raw.get("ticker", "")).upper()
            if ticker not in wanted or ticker in by_ticker:
                continue
            cik_str = raw.get("cik_str")
            title = str(raw.get("title", ""))
            if cik_str is None:
                continue
            by_ticker[ticker] = Company(
                ticker=ticker,
                cik=str(int(cik_str)).zfill(10),
                title=title,
            )
        missing = [t for t in wanted if t not in by_ticker]
        if missing:
            raise UnknownTickerError(missing)
        return {t: by_ticker[t] for t in wanted}

    async def list_filings(
        self,
        company: Company,
        *,
        forms: Sequence[str],
        years: frozenset[int],
    ) -> list[Filing]:
        """List matching filings from submissions JSON plus overlapping extra files."""
        wanted_forms = {f.casefold() for f in forms}
        main = await self._get_json(SUBMISSIONS_URL.format(cik=company.cik))
        if not isinstance(main, dict):
            raise EdgarProtocolError(f"submissions for {company.cik} is not an object")
        filings_obj = main.get("filings")
        if not isinstance(filings_obj, dict):
            raise EdgarProtocolError(f"submissions for {company.cik} missing filings")
        recent = filings_obj.get("recent")
        found: dict[str, Filing] = {}
        for filing in self._filings_from_columnar(company, recent, wanted_forms, years):
            found[filing.accession] = filing

        extra_files = filings_obj.get("files") or []
        if not isinstance(extra_files, list):
            raise EdgarProtocolError(f"submissions files for {company.cik} is not a list")
        window_start = date(min(years), 1, 1)
        window_end = date(max(years) + 1, 12, 31)
        for extra in extra_files:
            if not isinstance(extra, dict):
                continue
            name = str(extra.get("name", ""))
            if not name or "/" in name or name.startswith("."):
                continue
            if not _filing_window_overlaps(
                str(extra.get("filingFrom", "")),
                str(extra.get("filingTo", "")),
                window_start,
                window_end,
            ):
                continue
            extra_payload = await self._get_json(SUBMISSIONS_FILE_URL.format(name=name))
            for filing in self._filings_from_columnar(company, extra_payload, wanted_forms, years):
                found.setdefault(filing.accession, filing)

        return sorted(found.values(), key=lambda f: (f.period, f.accession))

    async def download_primary(self, filing: Filing) -> bytes:
        """Download the primary document bytes for a filing."""
        response = await self._get(filing.url)
        return response.content

    def _filings_from_columnar(
        self,
        company: Company,
        payload: object,
        wanted_forms: set[str],
        years: frozenset[int],
    ) -> list[Filing]:
        columns = _as_columnar(payload)
        n = len(columns["form"])
        out: list[Filing] = []
        for i in range(n):
            form = str(columns["form"][i])
            if form.casefold() not in wanted_forms:
                continue
            filing_date = str(columns["filingDate"][i])
            report_date = str(columns["reportDate"][i] or "")
            period = (report_date.strip() or filing_date.strip())[:10]
            if len(period) < 4 or not period[:4].isdigit():
                continue
            if int(period[:4]) not in years:
                continue
            accession = str(columns["accessionNumber"][i])
            document = str(columns["primaryDocument"][i]).rsplit("/", 1)[-1]
            if not accession or not document or document in {".", ".."}:
                continue
            out.append(
                Filing(
                    ticker=company.ticker,
                    cik=company.cik,
                    form=form,
                    period=period,
                    filing_date=filing_date[:10],
                    accession=accession,
                    primary_document=document,
                    url=archive_url(company.cik, accession, document),
                )
            )
        return out

    async def _get_json(self, url: str) -> object:
        response = await self._get(url)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise EdgarProtocolError(f"invalid JSON from {url}") from exc

    async def _get(self, url: str) -> httpx.Response:
        delay = INITIAL_BACKOFF_S
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._limiter.acquire()
            try:
                response = await self._client.get(url)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("EDGAR transport error %s attempt %s: %s", url, attempt, exc)
                if attempt == self._max_attempts:
                    break
                await self._backoff(delay)
                delay = min(delay * 2, MAX_BACKOFF_S)
                continue

            if response.status_code == 403:
                raise EdgarAccessError(
                    "SEC returned 403 for "
                    f"{url}. Declare SEC_USER_AGENT with a contact email "
                    "(https://www.sec.gov/os/accessing-edgar-data)."
                )
            if response.status_code in RETRY_STATUSES:
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
                logger.warning(
                    "EDGAR HTTP %s for %s attempt %s",
                    response.status_code,
                    url,
                    attempt,
                )
                if attempt == self._max_attempts:
                    break
                await self._backoff(_retry_wait(response, delay))
                delay = min(delay * 2, MAX_BACKOFF_S)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise EdgarError(f"EDGAR HTTP {response.status_code} for {url}") from exc
            return response

        raise EdgarUnavailableError(
            f"EDGAR unavailable for {url} after {self._max_attempts} attempts"
        ) from last_error

    async def _backoff(self, seconds: float) -> None:
        await self._sleep(seconds)


def _retry_wait(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return fallback
    try:
        parsed = float(raw)
    except ValueError:
        return fallback
    if parsed < 0:
        return fallback
    return min(parsed, MAX_RETRY_AFTER_S)


def _as_columnar(payload: object) -> Mapping[str, list[object]]:
    if not isinstance(payload, dict):
        raise EdgarProtocolError("filings payload is not an object")
    required = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    missing = [key for key in required if key not in payload]
    if missing:
        raise EdgarProtocolError(f"filings payload missing columns: {', '.join(missing)}")
    columns: dict[str, list[object]] = {}
    length: int | None = None
    for key in required:
        value = payload[key]
        if not isinstance(value, list):
            raise EdgarProtocolError(f"filings column {key} is not a list")
        if length is None:
            length = len(value)
        elif len(value) != length:
            raise EdgarProtocolError("filings columns have unequal lengths")
        columns[key] = value
    return columns


def _filing_window_overlaps(
    filing_from: str,
    filing_to: str,
    window_start: date,
    window_end: date,
) -> bool:
    try:
        start = date.fromisoformat(filing_from[:10])
        end = date.fromisoformat(filing_to[:10])
    except ValueError:
        return False
    return start <= window_end and end >= window_start
