from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from capitalcite.agent.fdic import lead_institutions_url, search_institutions_url
from capitalcite.agent.http import HttpTransportError, HttpxJsonClient
from capitalcite.agent.xbrl import companyfacts_url
from capitalcite.cli import app, metrics_fixtures
from capitalcite.holdings import HoldingCompany, holding_companies, holding_company
from capitalcite.ingest.rate_limit import shared_limiter
from capitalcite.kg.types import Bank
from capitalcite.metrics import MetricsStore, run_extract
from capitalcite.metrics.extract import (
    FixtureBatch,
    LiveBatch,
    financials_url,
    format_fetched_at,
)
from capitalcite.metrics.snapshot import (
    FINANCIAL_FIELDS,
    Cert,
    MetricsInputError,
    MetricsUnavailable,
    trim_companyfacts,
    trim_financials,
)

runner = CliRunner()
ROOT = metrics_fixtures()


def test_bankfind_urls_use_api_fdic_gov() -> None:
    search = search_institutions_url("")
    lead = lead_institutions_url("EXAMPLE BANK")
    financials = financials_url(Cert(value="1"))
    for url in (search, lead, financials):
        assert "banks.data.fdic.gov" not in url
    assert search.startswith("https://api.fdic.gov/banks/institutions")
    assert lead.startswith("https://api.fdic.gov/banks/institutions")
    assert "ACTIVE%3A1" in lead or "ACTIVE:1" in lead
    assert financials.startswith("https://api.fdic.gov/banks/financials")


def test_sources_do_not_use_the_old_host_or_fdic_cert() -> None:
    parts: list[str] = []
    for path in Path("src/capitalcite").rglob("*.py"):
        if path.relative_to("src/capitalcite").parts[:1] == ("kg",):
            continue
        parts.append(path.read_text(encoding="utf-8"))
    blob = "\n".join(parts)
    assert "banks.data.fdic.gov" not in blob
    assert "fdic_cert" not in blob
    extract_files = [
        Path("src/capitalcite/holdings.py"),
        Path("src/capitalcite/agent/fdic.py"),
        Path("src/capitalcite/agent/xbrl.py"),
        *Path("src/capitalcite/metrics").rglob("*.py"),
    ]
    extract = "\n".join(path.read_text(encoding="utf-8") for path in extract_files)
    assert "graph_snapshot" not in extract
    assert "fdic_cert" not in extract


def test_holdings_match_graph_parent_child_identity() -> None:
    snapshot = json.loads(Path("evals/golden/graph_snapshot.json").read_text(encoding="utf-8"))
    banks = {bank["rssd"]: bank for bank in snapshot["banks"]}
    linked = []
    for edge in snapshot["parents"]:
        parent = banks[edge["parent_rssd"]]
        child = banks[edge["child_rssd"]]
        linked.append((parent["ticker"], parent["cik"], child["name"]))
    companies = holding_companies()
    got = [(company.ticker, company.cik) for company in companies]
    assert got == [(ticker, cik) for ticker, cik, _name in linked]
    by_ticker = {company.ticker: company.lead_bank_name for company in companies}
    assert by_ticker["JPM"] == "JPMORGAN CHASE BANK, NATIONAL ASSOCIATION"
    assert by_ticker["BAC"] == "BANK OF AMERICA, NATIONAL ASSOCIATION"
    assert by_ticker["WFC"] == "WELLS FARGO BANK, NATIONAL ASSOCIATION"
    assert by_ticker["C"] == "Citibank, National Association"
    assert ("C", "0000831001", "CITIBANK, N.A.") in linked
    assert set(HoldingCompany.model_fields) == {"ticker", "cik", "lead_bank_name"}


async def test_xbrl_fact_requests_holding_cik() -> None:
    from capitalcite.agent.xbrl import XbrlClient

    seen: list[str] = []

    class Client:
        async def get_json(self, url: str) -> object:
            seen.append(url)
            return {
                "facts": {
                    "us-gaap": {
                        "Assets": {"units": {"USD": [{"val": 15, "end": "2024-12-31", "fy": 2024}]}}
                    }
                }
            }

    text = await XbrlClient(Client()).fact("jpm", "Assets", None)
    assert text == "15"
    assert seen == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json"]
    with pytest.raises(ValueError, match="unknown ticker"):
        await XbrlClient(Client()).fact("ZZ", "Assets", None)


def test_trim_companyfacts_keeps_numeric_observations_and_is_idempotent() -> None:
    raw = json.dumps(
        {
            "cik": 19617,
            "entityName": "EXAMPLE",
            "facts": {
                "us-gaap": {
                    "Liabilities": {
                        "label": "Liabilities",
                        "description": "drop",
                        "units": {
                            "USD": [
                                {"end": "2024-12-31", "val": 2, "fy": 2024, "frame": "CY2024"},
                                {"end": "2023-12-31", "val": "skip"},
                                {"end": "2023-12-31", "val": 1, "fy": 2023},
                            ]
                        },
                    },
                    "Assets": {"units": {"USD": [{"end": "2024-12-31", "val": 3.0, "fy": 2024}]}},
                }
            },
        }
    ).encode()
    once = trim_companyfacts(raw)
    assert trim_companyfacts(once) == once
    payload = json.loads(once)
    assert payload["cik"] == "0000019617"
    assert "REPDTE" not in once.decode()
    assert "frame" not in once.decode()
    assert "label" not in once.decode()
    facts = payload["facts"]["us-gaap"]
    assert set(facts) == {"Assets", "Liabilities"}
    ends = [row["end"] for row in facts["Liabilities"]["units"]["USD"]]
    assert ends == ["2023-12-31", "2024-12-31"]


def test_trim_financials_projects_seven_fields_and_is_idempotent() -> None:
    raw = {
        "meta": {"total": 2, "parameters": {"limit": "10000"}},
        "data": [
            {
                "score": 1,
                "data": {
                    "CERT": 628,
                    "REPDTE": "20240630",
                    "ASSET": 1.0,
                    "DEP": 2.5,
                    "NIMY": None,
                    "EEFFR": 0.0,
                    "NETINC": -3,
                    "ID": "drop",
                },
            },
            {"data": {"CERT": "628", "REPDTE": 20231231, "ASSET": 4}},
        ],
    }
    once = trim_financials(json.dumps(raw).encode())
    assert trim_financials(once) == once
    page = json.loads(once)
    assert page["meta"] == {"total": 2}
    assert "entityName" not in once.decode()
    assert "score" not in once.decode()
    first, second = (row["data"] for row in page["data"])
    assert list(first) == [
        "ASSET",
        "CERT",
        "DEP",
        "EEFFR",
        "NETINC",
        "NIMY",
        "REPDTE",
    ]
    assert first["REPDTE"] == "20231231"
    assert first["ASSET"] == 4
    assert first["NIMY"] is None
    assert second["ASSET"] == 1
    assert isinstance(second["ASSET"], int)
    assert second["DEP"] == 2.5
    assert second["EEFFR"] == 0
    assert isinstance(second["EEFFR"], int)


def test_trim_financials_rejects_bool() -> None:
    raw = {
        "meta": {"total": 1},
        "data": [
            {
                "data": {
                    "CERT": True,
                    "REPDTE": "20231231",
                    "ASSET": 1,
                    "DEP": 1,
                    "NIMY": 1,
                    "EEFFR": 1,
                    "NETINC": 1,
                }
            }
        ],
    }
    with pytest.raises(MetricsInputError, match="CERT"):
        trim_financials(json.dumps(raw).encode())


def test_non_finite_financials_are_not_json_numbers() -> None:
    text = '{"meta":{"total":1},"data":[{"data":{"CERT":1,"REPDTE":"20231231","ASSET":NaN}}]}'
    with pytest.raises(MetricsInputError):
        trim_financials(text.encode())


def test_format_fetched_at_requires_a_timezone() -> None:
    stamped = format_fetched_at(datetime(2026, 10, 23, 11, 0, tzinfo=UTC))
    assert stamped == "2026-10-23T11:00:00Z"
    with pytest.raises(MetricsInputError):
        format_fetched_at(datetime(2026, 10, 23, 11, 0))


async def test_live_batch_trims_and_keeps_the_institutions_certificate(tmp_path: Path) -> None:
    bank = Bank(
        rssd="852218",
        name="JPMORGAN CHASE BANK, NATIONAL ASSOCIATION",
        fdic_cert="999",
    )
    company = holding_company("JPM")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        url = str(request.url)
        if "/institutions" in url:
            return httpx.Response(
                200,
                json={
                    "meta": {"total": 1},
                    "data": [
                        {
                            "data": {
                                "NAME": "JPMorgan Chase Bank, National Association",
                                "CERT": 628,
                                "ACTIVE": 1,
                            }
                        }
                    ],
                },
            )
        if "companyfacts" in url:
            return httpx.Response(200, content=_facts_body())
        if "/financials" in url:
            return httpx.Response(200, content=_financials_body())
        return httpx.Response(404)

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(handler),
    )
    store = MetricsStore(tmp_path)
    receipt = await run_extract(
        (company,),
        LiveBatch(sec=client, fdic=client, clock=_clock),
        store,
    )
    written = receipt.companies[0]
    assert written.lead.cert.value == "628"
    assert bank.fdic_cert not in str(written.financials.path)
    assert written.financials.path.name == "628.json"
    xbrl_trimmed = trim_companyfacts(_facts_body())
    fin_trimmed = trim_financials(_financials_body())
    assert written.xbrl.path.read_bytes() == xbrl_trimmed
    assert written.financials.path.read_bytes() == fin_trimmed
    assert written.xbrl.metadata.url == companyfacts_url(company)
    assert written.financials.metadata.url == financials_url(Cert(value="628"))
    assert written.xbrl.metadata.sha256 == hashlib.sha256(xbrl_trimmed).hexdigest()
    assert written.financials.metadata.sha256 == hashlib.sha256(fin_trimmed).hexdigest()
    assert written.xbrl.metadata.sha256 != hashlib.sha256(_facts_body()).hexdigest()
    assert written.financials.metadata.sha256 != hashlib.sha256(_financials_body()).hexdigest()
    assert written.xbrl.metadata.bytes == len(xbrl_trimmed)
    assert written.financials.metadata.bytes == len(fin_trimmed)
    assert written.xbrl.metadata.fetched_at == "2026-10-23T11:00:00Z"
    assert b"REPDTE" not in written.xbrl.path.read_bytes()
    assert b"entityName" not in written.financials.path.read_bytes()
    assert b'"cik"' in written.xbrl.path.read_bytes()
    assert b'"CERT"' in written.financials.path.read_bytes()
    links = store.read_leads()
    assert [link.ticker for link in links] == ["JPM"]
    lead_text = (tmp_path / "raw" / "metrics" / "lead_banks.json").read_text(encoding="utf-8")
    assert "ASSET" not in lead_text
    assert "facts" not in lead_text
    assert written.xbrl.path != written.financials.path
    assert any("/financials" in url for url in seen)
    assert all(not url.startswith("https://banks.data.fdic.gov") for url in seen)


async def test_financials_301_writes_nothing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/institutions" in url:
            return httpx.Response(200, json=_institution_page())
        if "companyfacts" in url:
            return httpx.Response(200, content=_facts_body())
        return httpx.Response(301, headers={"location": "https://api.fdic.gov/banks/financials"})

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MetricsUnavailable, match="HTTP 301"):
        await run_extract(
            (holding_company("JPM"),),
            LiveBatch(sec=client, fdic=client, clock=_clock),
            MetricsStore(tmp_path),
        )
    assert list(tmp_path.rglob("*")) == []


async def test_truncated_financials_page_writes_nothing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/institutions" in url:
            return httpx.Response(200, json=_institution_page())
        if "companyfacts" in url:
            return httpx.Response(200, content=_facts_body())
        body = json.loads(_financials_body())
        body["meta"]["total"] = 2
        return httpx.Response(200, json=body)

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MetricsInputError, match="truncated"):
        await run_extract(
            (holding_company("JPM"),),
            LiveBatch(sec=client, fdic=client, clock=_clock),
            MetricsStore(tmp_path),
        )
    assert list(tmp_path.rglob("*")) == []


async def test_two_active_name_matches_write_nothing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "meta": {"total": 2},
                "data": [
                    {
                        "data": {
                            "NAME": "JPMORGAN CHASE BANK, NATIONAL ASSOCIATION",
                            "CERT": 628,
                            "ACTIVE": 1,
                        }
                    },
                    {
                        "data": {
                            "NAME": "JPMORGAN CHASE BANK, NATIONAL ASSOCIATION",
                            "CERT": 3510,
                            "ACTIVE": 1,
                        }
                    },
                ],
            },
        )

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MetricsInputError, match="one active"):
        await run_extract(
            (holding_company("JPM"),),
            LiveBatch(sec=client, fdic=client, clock=_clock),
            MetricsStore(tmp_path),
        )
    assert list(tmp_path.rglob("*")) == []


def test_cli_fixtures_are_byte_identical(tmp_path: Path) -> None:
    args = [
        "metrics",
        "extract",
        "--fixtures",
        "--tickers",
        "JPM,BAC,WFC,C",
        "--output-dir",
        str(tmp_path),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.stdout + first.stderr
    assert "metrics extract xbrl=4 financials=4" in first.stdout
    _assert_tree_matches_fixtures(tmp_path, first.stdout)
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.stdout + second.stderr
    assert second.stdout == first.stdout
    _assert_tree_matches_fixtures(tmp_path, second.stdout)
    links = MetricsStore(tmp_path).read_leads()
    assert [link.ticker for link in links] == [company.ticker for company in holding_companies()]


def test_cli_fixtures_resolve_from_another_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert not Path("tests/fixtures/metrics").exists()
    result = runner.invoke(
        app,
        [
            "metrics",
            "extract",
            "--fixtures",
            "--tickers",
            "JPM,BAC,WFC,C",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    _assert_tree_matches_fixtures(tmp_path / "out", result.stdout)


def test_cli_fixtures_do_not_need_sec_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from capitalcite.config import get_settings

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    get_settings.cache_clear()
    result = runner.invoke(
        app,
        [
            "metrics",
            "extract",
            "--fixtures",
            "--tickers",
            "JPM,BAC,WFC,C",
            "--output-dir",
            str(tmp_path),
        ],
    )
    get_settings.cache_clear()
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "metrics extract xbrl=4 financials=4" in result.stdout


def test_cli_fixtures_do_not_open_a_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kwargs: object) -> None:
        raise AssertionError("fixtures constructed an HTTP client")

    monkeypatch.setattr("capitalcite.agent.http.HttpxJsonClient", boom)
    result = runner.invoke(
        app,
        ["metrics", "extract", "--fixtures", "--tickers", "JPM", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    leads = json.loads((tmp_path / "raw" / "metrics" / "lead_banks.json").read_text())
    cert = str(leads[0]["cert"])
    page = json.loads((tmp_path / "raw" / "fdic" / "financials" / f"{cert}.json").read_text())
    assert f"rows={page['meta']['total']}" in result.stdout


def test_cli_rejects_unknown_ticker_and_dishonest_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = runner.invoke(
        app,
        ["metrics", "extract", "--fixtures", "--tickers", "ZZ", "--output-dir", str(tmp_path)],
    )
    assert unknown.exit_code == 1
    assert "unknown ticker ZZ" in unknown.stdout + unknown.stderr
    assert list(tmp_path.rglob("*")) == []

    flipped = _copy_fixtures(tmp_path / "flipped")
    payload = flipped / "xbrl" / "JPM.json"
    data = bytearray(payload.read_bytes())
    data[10] ^= 1
    payload.write_bytes(bytes(data))
    _invoke_fixtures(monkeypatch, flipped, tmp_path / "out-flipped")

    hashed = _copy_fixtures(tmp_path / "hashed")
    meta_path = hashed / "xbrl" / "JPM.metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sha256"] = "0" * 64
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _invoke_fixtures(monkeypatch, hashed, tmp_path / "out-hashed")

    renamed = _copy_fixtures(tmp_path / "renamed")
    leads = json.loads((renamed / "lead_banks.json").read_text(encoding="utf-8"))
    leads[0]["ticker"] = "ZZZ"
    (renamed / "lead_banks.json").write_text(json.dumps(leads), encoding="utf-8")
    _invoke_fixtures(monkeypatch, renamed, tmp_path / "out-renamed")


def test_live_command_limits_sec_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            constructed.append(kwargs)

        async def get_bytes(self, url: str) -> bytes:
            raise HttpTransportError(url)

    monkeypatch.setattr("capitalcite.agent.http.HttpxJsonClient", FakeClient)
    result = runner.invoke(
        app,
        ["metrics", "extract", "--tickers", "JPM", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert "error:" in result.stdout + result.stderr
    assert list(tmp_path.rglob("*")) == []
    assert len(constructed) == 2
    from capitalcite.agent.fdic import BANKFIND_TIMEOUT, bankfind_user_agent

    assert constructed[0]["user_agent"] != constructed[1]["user_agent"]
    assert constructed[0]["limiter"] is shared_limiter()
    assert constructed[0]["timeout"] == 120.0
    assert constructed[1]["user_agent"] == bankfind_user_agent()
    assert "@" not in str(constructed[1]["user_agent"])
    assert constructed[1]["timeout"] == BANKFIND_TIMEOUT
    assert constructed[1].get("limiter") is None


def test_live_command_maps_typed_http_errors_not_message_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from capitalcite.agent.http import HttpStatusError

    class PrefixLookalike:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def get_bytes(self, url: str) -> bytes:
            raise ValueError("HTTP 301 lookalike")

    monkeypatch.setattr("capitalcite.agent.http.HttpxJsonClient", PrefixLookalike)
    lookalike = runner.invoke(
        app,
        ["metrics", "extract", "--tickers", "JPM", "--output-dir", str(tmp_path / "lookalike")],
    )
    assert lookalike.exit_code == 1, lookalike.stdout + lookalike.stderr

    class StatusFailure:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def get_bytes(self, url: str) -> bytes:
            raise HttpStatusError(301, url)

    monkeypatch.setattr("capitalcite.agent.http.HttpxJsonClient", StatusFailure)
    status = runner.invoke(
        app,
        ["metrics", "extract", "--tickers", "JPM", "--output-dir", str(tmp_path / "status")],
    )
    assert status.exit_code == 2, status.stdout + status.stderr
    assert "HTTP 301" in status.stdout + status.stderr


def _clock() -> datetime:
    return datetime(2026, 10, 23, 11, 0, tzinfo=UTC)


def _facts_body() -> bytes:
    return json.dumps(
        {
            "cik": 19617,
            "entityName": "JPMORGAN CHASE & CO",
            "facts": {
                "us-gaap": {
                    "Assets": {
                        "label": "Assets",
                        "units": {
                            "USD": [
                                {
                                    "end": "2024-12-31",
                                    "val": 2,
                                    "fy": 2024,
                                    "fp": "FY",
                                    "form": "10-K",
                                    "accn": "000",
                                    "filed": "2025-02-01",
                                    "frame": "CY2024",
                                }
                            ]
                        },
                    }
                }
            },
        }
    ).encode()


def _financials_body() -> bytes:
    return json.dumps(
        {
            "meta": {"total": 1, "parameters": {"limit": "10000"}},
            "data": [
                {
                    "score": 1,
                    "data": {
                        "CERT": 628,
                        "REPDTE": "20231231",
                        "ASSET": 1.0,
                        "DEP": 2,
                        "ID": "drop-me",
                    },
                }
            ],
        }
    ).encode()


def _institution_page() -> dict[str, object]:
    return {
        "meta": {"total": 1},
        "data": [
            {
                "data": {
                    "NAME": "JPMORGAN CHASE BANK, NATIONAL ASSOCIATION",
                    "CERT": 628,
                    "ACTIVE": 1,
                }
            }
        ],
    }


def _assert_tree_matches_fixtures(output: Path, stdout: str) -> None:
    leads = json.loads((ROOT / "lead_banks.json").read_text(encoding="utf-8"))
    assert (output / "raw" / "metrics" / "lead_banks.json").read_bytes() == (
        ROOT / "lead_banks.json"
    ).read_bytes()
    for link in leads:
        ticker = str(link["ticker"])
        cert = str(link["cert"])
        for kind, name in (("xbrl", ticker), ("fdic", cert)):
            payload = ROOT / kind / f"{name}.json"
            meta = ROOT / kind / f"{name}.metadata.json"
            if kind == "xbrl":
                written = output / "raw" / "xbrl" / f"{name}.json"
            else:
                written = output / "raw" / "fdic" / "financials" / f"{name}.json"
            assert written.read_bytes() == payload.read_bytes()
            assert written.with_name(f"{name}.metadata.json").read_bytes() == meta.read_bytes()
        page = json.loads((output / "raw" / "fdic" / "financials" / f"{cert}.json").read_bytes())
        assert f"financials cert={cert} rows={page['meta']['total']}" in stdout
        for row in page["data"]:
            assert list(row["data"]) == sorted(FINANCIAL_FIELDS)
        xbrl = json.loads((output / "raw" / "xbrl" / f"{ticker}.json").read_bytes())
        assert "cik" in xbrl
        assert "REPDTE" not in (output / "raw" / "xbrl" / f"{ticker}.json").read_text()
        assert (
            "entityName"
            not in (output / "raw" / "fdic" / "financials" / f"{cert}.json").read_text()
        )


def _copy_fixtures(dest: Path) -> Path:
    shutil.copytree(ROOT, dest)
    return dest


def _invoke_fixtures(monkeypatch: pytest.MonkeyPatch, fixtures: Path, output: Path) -> None:
    monkeypatch.setattr("capitalcite.cli.metrics_fixtures", lambda: fixtures)
    result = runner.invoke(
        app,
        [
            "metrics",
            "extract",
            "--fixtures",
            "--tickers",
            "JPM,BAC,WFC,C",
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 1, result.stdout + result.stderr
    assert "error:" in result.stdout + result.stderr
    assert list(output.rglob("*")) == []


def test_fixture_batch_accepts_the_committed_tree() -> None:
    loaded = asyncio.run(FixtureBatch(ROOT).load(holding_companies()))
    assert [item.lead.company.ticker for item in loaded.companies] == [
        company.ticker for company in holding_companies()
    ]
    assert FINANCIAL_FIELDS == (
        "CERT",
        "REPDTE",
        "ASSET",
        "DEP",
        "NIMY",
        "EEFFR",
        "NETINC",
    )
    for item in loaded.companies:
        page = json.loads(item.financials.body)
        assert item.financials.rows == page["meta"]["total"] == len(page["data"]) >= 1
        for row in page["data"]:
            assert list(row["data"]) == sorted(FINANCIAL_FIELDS)
