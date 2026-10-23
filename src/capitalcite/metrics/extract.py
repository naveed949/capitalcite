from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

from pydantic import ValidationError

from capitalcite.agent.fdic import BANKFIND_ORIGIN, Institution, institutions, lead_institutions_url
from capitalcite.agent.http import BytesClient, HttpClientError
from capitalcite.agent.xbrl import companyfacts_url
from capitalcite.holdings import HoldingCompany, holding_companies
from capitalcite.metrics.snapshot import (
    FINANCIAL_FIELDS,
    Cert,
    ExtractReceipt,
    FinancialsBody,
    LeadBank,
    LeadLink,
    LoadedCompany,
    LoadedSnapshots,
    MetricsInputError,
    MetricsStore,
    MetricsUnavailable,
    SnapshotMetadata,
    XbrlBody,
    require_companyfacts,
    require_financials,
    trim_companyfacts,
    trim_financials,
)


class SnapshotBatch(Protocol):
    async def load(self, companies: tuple[HoldingCompany, ...]) -> LoadedSnapshots:
        """One LoadedCompany per input, same order. Raise rather than return a partial tuple."""


def financials_url(cert: Cert) -> str:
    params = {
        "filters": f"CERT:{cert}",
        "fields": ",".join(FINANCIAL_FIELDS),
        "limit": "10000",
        "sort_by": "REPDTE",
        "sort_order": "ASC",
        "format": "json",
    }
    return f"{BANKFIND_ORIGIN}/financials?" + urlencode(params)


def format_fetched_at(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise MetricsInputError("fetched_at requires tzinfo")
    utc = moment.astimezone(UTC).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> datetime:
    return datetime.now(UTC)


def one_lead(rows: tuple[Institution, ...], legal_name: str) -> str | None:
    wanted = _collapse(legal_name)
    matched = [
        row.cert for row in rows if row.active and row.cert and _collapse(row.name) == wanted
    ]
    if len(matched) != 1:
        return None
    return matched[0]


class LiveBatch:
    def __init__(
        self,
        *,
        sec: BytesClient,
        fdic: BytesClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._sec = sec
        self._fdic = fdic
        self._clock = clock

    async def load(self, companies: tuple[HoldingCompany, ...]) -> LoadedSnapshots:
        loaded: list[LoadedCompany] = []
        for company in companies:
            lead = await self._lead(company)
            xbrl_body = await self._xbrl(company)
            financials_body = await self._financials(lead)
            loaded.append(LoadedCompany(lead=lead, xbrl=xbrl_body, financials=financials_body))
        return LoadedSnapshots(companies=tuple(loaded))

    async def _lead(self, company: HoldingCompany) -> LeadBank:
        try:
            url = lead_institutions_url(company.lead_bank_name)
        except ValueError as exc:
            raise MetricsInputError(str(exc)) from exc
        body = await self._get(self._fdic, url)
        try:
            payload: object = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MetricsInputError("institutions response was not JSON") from exc
        try:
            total, rows = institutions(payload)
        except ValueError as exc:
            raise MetricsInputError(str(exc)) from exc
        if total is None or total != len(rows):
            raise MetricsInputError(f"institutions page for {company.ticker} is truncated")
        cert_text = one_lead(rows, company.lead_bank_name)
        if cert_text is None:
            raise MetricsInputError(
                f"lead bank name for {company.ticker} did not match one active institution"
            )
        try:
            cert = Cert(value=cert_text)
        except ValidationError as exc:
            raise MetricsInputError(f"lead certificate for {company.ticker} is invalid") from exc
        return LeadBank(company=company, cert=cert)

    async def _xbrl(self, company: HoldingCompany) -> XbrlBody:
        url = companyfacts_url(company)
        body = await self._get(self._sec, url)
        require_companyfacts(body, company)
        return XbrlBody(url=url, body=body, fetched_at=format_fetched_at(self._clock()))

    async def _financials(self, lead: LeadBank) -> FinancialsBody:
        url = financials_url(lead.cert)
        body = await self._get(self._fdic, url)
        parsed = require_financials(body, lead.cert)
        return FinancialsBody(
            url=url,
            body=body,
            fetched_at=format_fetched_at(self._clock()),
            rows=parsed.total,
        )

    async def _get(self, client: BytesClient, url: str) -> bytes:
        try:
            return await client.get_bytes(url)
        except HttpClientError as exc:
            raise MetricsUnavailable(str(exc)) from exc
        except ValueError as exc:
            raise MetricsInputError(str(exc)) from exc


class FixtureBatch:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def load(self, companies: tuple[HoldingCompany, ...]) -> LoadedSnapshots:
        table = holding_companies()
        links = _read_links(self.root / "lead_banks.json")
        if tuple(link.ticker for link in links) != tuple(company.ticker for company in table):
            raise MetricsInputError("fixture lead_banks.json tickers do not match holdings")
        by_ticker: dict[str, LoadedCompany] = {}
        for link, company in zip(links, table, strict=True):
            by_ticker[company.ticker] = _load_fixture_company(self.root, company, link)
        selected: list[LoadedCompany] = []
        for company in companies:
            loaded = by_ticker.get(company.ticker)
            if loaded is None or loaded.lead.company != company:
                raise MetricsInputError(f"fixture has no {company.ticker}")
            selected.append(loaded)
        return LoadedSnapshots(companies=tuple(selected))


async def run_extract(
    companies: tuple[HoldingCompany, ...],
    batch: SnapshotBatch,
    store: MetricsStore,
) -> ExtractReceipt:
    if not companies:
        raise MetricsInputError("metrics extract needs at least one ticker")
    tickers = [company.ticker for company in companies]
    if len(tickers) != len(set(tickers)):
        raise MetricsInputError("duplicate ticker in one extract")
    loaded = await batch.load(companies)
    if tuple(item.lead.company.ticker for item in loaded.companies) != tuple(tickers):
        raise MetricsInputError("loaded tickers do not match the request")
    for company, item in zip(companies, loaded.companies, strict=True):
        if item.lead.company != company:
            raise MetricsInputError(f"loaded company does not match {company.ticker}")
        if item.xbrl.url != companyfacts_url(company):
            raise MetricsInputError(f"xbrl url does not match {company.ticker}")
        if item.financials.url != financials_url(item.lead.cert):
            raise MetricsInputError(f"financials url does not match {item.lead.cert}")
        if item.financials.rows < 1:
            raise MetricsInputError(f"financials page for {company.ticker} is empty")
    return store.write_run(loaded)


def _collapse(value: str) -> str:
    return " ".join(value.split()).casefold()


def _read_links(path: Path) -> tuple[LeadLink, ...]:
    if not path.is_file():
        raise MetricsInputError("fixture lead_banks.json is missing")
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetricsInputError("fixture lead_banks.json was not JSON") from exc
    if not isinstance(payload, list):
        raise MetricsInputError("fixture lead_banks.json was not a list")
    links: list[LeadLink] = []
    for item in payload:
        try:
            links.append(LeadLink.model_validate(item))
        except ValidationError as exc:
            raise MetricsInputError("fixture lead_banks.json row is invalid") from exc
    return tuple(links)


def _load_fixture_company(root: Path, company: HoldingCompany, link: LeadLink) -> LoadedCompany:
    if link.ticker != company.ticker:
        raise MetricsInputError("fixture lead_banks.json tickers do not match holdings")
    try:
        cert = Cert(value=link.cert)
    except ValidationError as exc:
        raise MetricsInputError(f"fixture certificate for {company.ticker} is invalid") from exc
    xbrl_path = root / "xbrl" / f"{company.ticker}.json"
    fin_path = root / "fdic" / f"{cert}.json"
    xbrl_bytes = _read(xbrl_path, company.ticker)
    fin_bytes = _read(fin_path, company.ticker)
    xbrl_meta = _sidecar(xbrl_path, xbrl_bytes, company.ticker)
    fin_meta = _sidecar(fin_path, fin_bytes, company.ticker)
    if xbrl_meta.url != companyfacts_url(company):
        raise MetricsInputError(f"fixture xbrl url does not match {company.ticker}")
    if fin_meta.url != financials_url(cert):
        raise MetricsInputError(f"fixture financials url does not match {cert}")
    if trim_companyfacts(xbrl_bytes) != xbrl_bytes:
        raise MetricsInputError(
            f"fixture companyfacts for {company.ticker} is not a trim fixed point"
        )
    if trim_financials(fin_bytes) != fin_bytes:
        raise MetricsInputError(f"fixture financials for {cert} is not a trim fixed point")
    require_companyfacts(xbrl_bytes, company)
    financials = require_financials(fin_bytes, cert)
    return LoadedCompany(
        lead=LeadBank(company=company, cert=cert),
        xbrl=XbrlBody(url=xbrl_meta.url, body=xbrl_bytes, fetched_at=xbrl_meta.fetched_at),
        financials=FinancialsBody(
            url=fin_meta.url,
            body=fin_bytes,
            fetched_at=fin_meta.fetched_at,
            rows=financials.total,
        ),
    )


def _read(path: Path, ticker: str) -> bytes:
    if not path.is_file():
        raise MetricsInputError(f"fixture file for {ticker} is missing")
    return path.read_bytes()


def _sidecar(path: Path, body: bytes, ticker: str) -> SnapshotMetadata:
    meta_path = path.with_name(f"{path.stem}.metadata.json")
    if not meta_path.is_file():
        raise MetricsInputError(f"fixture metadata for {ticker} is missing")
    try:
        meta = SnapshotMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise MetricsInputError(f"fixture metadata for {ticker} is invalid") from exc
    digest = hashlib.sha256(body).hexdigest()
    if meta.sha256 != digest or meta.bytes != len(body):
        raise MetricsInputError(f"fixture sha256 does not match {path.name}")
    return meta
