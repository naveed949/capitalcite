from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from capitalcite.cli import app
from capitalcite.ingest.client import (
    EdgarAccessError,
    EdgarClient,
    EdgarUnavailableError,
    UnknownTickerError,
)
from capitalcite.ingest.parse import ParseError, parse_forms, parse_tickers, parse_years
from capitalcite.ingest.rate_limit import RateLimiter
from capitalcite.ingest.service import run_ingest
from capitalcite.ingest.store import FilingStore
from tests.edgar_replay import PRIMARY_HTML, USER_AGENT, RecordingTransport, noop_sleep

runner = CliRunner()

EXPECTED_10K = {
    ("JPM", "2023-12-31", "0000019617-24-000225", "jpm-20231231.htm"),
    ("JPM", "2024-12-31", "0000019617-25-000270", "jpm-20241231.htm"),
    ("JPM", "2025-12-31", "0001628280-26-008131", "jpm-20251231.htm"),
    ("BAC", "2023-12-31", "0000070858-24-000122", "bac-20231231.htm"),
    ("BAC", "2024-12-31", "0000070858-25-000139", "bac-20241231.htm"),
    ("BAC", "2025-12-31", "0000070858-26-000157", "bac-20251231.htm"),
    ("WFC", "2023-12-31", "0000072971-24-000064", "wfc-20231231.htm"),
    ("WFC", "2024-12-31", "0000072971-25-000066", "wfc-20241231.htm"),
    ("WFC", "2025-12-31", "0000072971-26-000133", "wfc-20251231_d2.htm"),
    ("C", "2023-12-31", "0000831001-24-000033", "c-20231231.htm"),
    ("C", "2024-12-31", "0000831001-25-000067", "c-20241231.htm"),
    ("C", "2025-12-31", "0000831001-26-000011", "c-20251231.htm"),
}

TICKERS = ("JPM", "BAC", "WFC", "C")
YEARS = frozenset({2023, 2024, 2025})


def _client(transport: httpx.AsyncBaseTransport | None = None) -> EdgarClient:
    return EdgarClient(
        user_agent=USER_AGENT,
        transport=transport or RecordingTransport(),
        limiter=RateLimiter(max_per_second=1000.0, sleeper=noop_sleep),
        sleeper=noop_sleep,
    )


def test_parse_years_range() -> None:
    assert parse_years("2023-2025") == frozenset({2023, 2024, 2025})
    assert parse_years("2024") == frozenset({2024})
    assert parse_years("2023,2025") == frozenset({2023, 2025})
    with pytest.raises(ParseError):
        parse_years("2025-2023")
    with pytest.raises(ParseError):
        parse_years("")


def test_parse_tickers_and_forms() -> None:
    assert parse_tickers("jpm, BAC, jpm") == ("JPM", "BAC")
    assert parse_forms("10-K,10-Q") == ("10-K", "10-Q")
    with pytest.raises(ParseError):
        parse_tickers("JP-M")
    with pytest.raises(ParseError):
        parse_forms("  ,  ")


@pytest.mark.asyncio
async def test_ingest_expected_10k_filings(tmp_path: Path) -> None:
    transport = RecordingTransport()
    store = FilingStore(tmp_path)
    async with _client(transport) as client:
        summary = await run_ingest(
            client=client,
            store=store,
            tickers=TICKERS,
            forms=("10-K",),
            years=YEARS,
        )

    stored = {(m.ticker, m.period, m.accession, m.primary_document) for m in summary.downloaded}
    assert stored == EXPECTED_10K
    assert summary.skipped_count == 0
    assert summary.failed_count == 0
    assert summary.downloaded_count == 12

    for meta in summary.downloaded:
        path = store.document_path(meta)
        assert path.read_bytes() == PRIMARY_HTML
        sidecar = store.load_metadata(meta)
        assert sidecar.ticker == meta.ticker
        assert sidecar.form == "10-K"
        assert sidecar.period == meta.period
        assert sidecar.accession == meta.accession
        assert sidecar.url == meta.url
        assert sidecar.bytes == len(PRIMARY_HTML)
        assert sidecar.sha256
        assert "sec.gov/Archives/edgar/data/" in sidecar.url

    assert not any("submissions-099.json" in url for url in transport.urls)
    assert any(url.endswith("/files/company_tickers.json") for url in transport.urls)
    assert all(ua == USER_AGENT for ua in transport.user_agents)


@pytest.mark.asyncio
async def test_ingest_is_idempotent(tmp_path: Path) -> None:
    transport = RecordingTransport()
    store = FilingStore(tmp_path)
    async with _client(transport) as client:
        first = await run_ingest(
            client=client,
            store=store,
            tickers=TICKERS,
            forms=("10-K",),
            years=YEARS,
        )
        second = await run_ingest(
            client=client,
            store=store,
            tickers=TICKERS,
            forms=("10-K",),
            years=YEARS,
        )

    assert first.downloaded_count == 12
    assert second.downloaded_count == 0
    assert second.skipped_count == 12
    archive_gets = [u for u in transport.urls if "/Archives/edgar/data/" in u]
    assert len(archive_gets) == 12


@pytest.mark.asyncio
async def test_unknown_ticker_fails_closed() -> None:
    async with _client() as client:
        with pytest.raises(UnknownTickerError) as exc:
            await client.lookup_companies(("JPM", "NOPE"))
    assert "NOPE" in str(exc.value)


@pytest.mark.asyncio
async def test_declared_user_agent_on_every_request() -> None:
    transport = RecordingTransport()
    async with _client(transport) as client:
        await client.lookup_companies(("JPM",))
    assert transport.urls
    assert all(ua == USER_AGENT for ua in transport.user_agents)


@pytest.mark.asyncio
async def test_retry_backoff_on_429() -> None:
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/files/company_tickers.json":
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, headers={"Retry-After": "1.5"}, text="slow down")
            body = (
                Path(__file__).parent / "fixtures" / "edgar" / "company_tickers.json"
            ).read_bytes()
            return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = EdgarClient(
        user_agent=USER_AGENT,
        transport=transport,
        limiter=RateLimiter(max_per_second=1000.0, sleeper=record_sleep),
        sleeper=record_sleep,
        max_attempts=5,
    )
    async with client:
        companies = await client.lookup_companies(("JPM",))
    assert companies["JPM"].cik == "0000019617"
    assert calls["n"] == 3
    assert 1.5 in sleeps


@pytest.mark.asyncio
async def test_retry_exhausted_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = EdgarClient(
        user_agent=USER_AGENT,
        transport=httpx.MockTransport(handler),
        limiter=RateLimiter(max_per_second=1000.0, sleeper=noop_sleep),
        sleeper=noop_sleep,
        max_attempts=3,
    )
    async with client:
        with pytest.raises(EdgarUnavailableError):
            await client.lookup_companies(("JPM",))


@pytest.mark.asyncio
async def test_forbidden_raises_access_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = EdgarClient(
        user_agent=USER_AGENT,
        transport=httpx.MockTransport(handler),
        limiter=RateLimiter(max_per_second=1000.0, sleeper=noop_sleep),
        sleeper=noop_sleep,
    )
    async with client:
        with pytest.raises(EdgarAccessError):
            await client.lookup_companies(("JPM",))


@pytest.mark.asyncio
async def test_rate_limiter_spaces_requests() -> None:
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["t"]

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(max_per_second=10.0, clock=monotonic, sleeper=sleep)
    for _ in range(11):
        await limiter.acquire()
    assert sleeps == [0.1] * 10
    assert clock["t"] == pytest.approx(1.0)


def test_cli_ingest_downloads_expected_filings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = RecordingTransport()

    def factory(user_agent: str) -> EdgarClient:
        assert "@" in user_agent
        return EdgarClient(
            user_agent=user_agent,
            transport=transport,
            limiter=RateLimiter(max_per_second=1000.0, sleeper=noop_sleep),
            sleeper=noop_sleep,
        )

    monkeypatch.setattr("capitalcite.cli.create_edgar_client", factory)
    result = runner.invoke(
        app,
        [
            "ingest",
            "--tickers",
            "JPM,BAC,WFC,C",
            "--forms",
            "10-K",
            "--years",
            "2023-2025",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "downloaded=12" in result.stdout
    assert "skipped=0" in result.stdout
    metadata_files = list(tmp_path.glob("raw/edgar/**/metadata.json"))
    assert len(metadata_files) == 12

    result2 = runner.invoke(
        app,
        [
            "ingest",
            "--tickers",
            "JPM,BAC,WFC,C",
            "--forms",
            "10-K",
            "--years",
            "2023-2025",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result2.exit_code == 0, result2.stdout + result2.stderr
    assert "downloaded=0" in result2.stdout
    assert "skipped=12" in result2.stdout


def test_cli_ingest_help() -> None:
    result = runner.invoke(
        app,
        ["ingest", "--help"],
        color=False,
        env={"COLUMNS": "120", "TERM": "dumb", "NO_COLOR": "1"},
    )
    assert result.exit_code == 0
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "--tickers" in text
    assert "--forms" in text
    assert "--years" in text


def test_cli_unknown_ticker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(user_agent: str) -> EdgarClient:
        return _client()

    monkeypatch.setattr("capitalcite.cli.create_edgar_client", factory)
    result = runner.invoke(
        app,
        [
            "ingest",
            "--tickers",
            "NOPE",
            "--forms",
            "10-K",
            "--years",
            "2024",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "unknown ticker" in result.stderr.lower() + result.stdout.lower()


@pytest.mark.asyncio
async def test_live_http_transport_is_blocked() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="live network is forbidden"):
            await client.get("https://www.sec.gov/files/company_tickers.json")
