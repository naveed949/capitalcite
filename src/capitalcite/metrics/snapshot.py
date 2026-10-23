from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from capitalcite.atomic import atomic_write_bytes, atomic_write_text
from capitalcite.holdings import HoldingCompany

FINANCIAL_FIELDS: tuple[str, ...] = (
    "CERT",
    "REPDTE",
    "ASSET",
    "DEP",
    "NIMY",
    "EEFFR",
    "NETINC",
)
_OBS_KEYS: tuple[str, ...] = ("start", "end", "val", "accn", "fy", "fp", "form", "filed")
_OBS_SORT: tuple[str, ...] = ("end", "fy", "fp", "form", "accn", "filed")
_CERT = re.compile(r"[1-9][0-9]{0,6}")


class MetricsError(Exception):
    pass


class MetricsInputError(MetricsError):
    """The inputs or the 200 body cannot be a snapshot."""


class MetricsUnavailable(MetricsError):
    """The network did not return a usable 200."""


class Cert(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: str

    @field_validator("value")
    @classmethod
    def digits(cls, value: str) -> str:
        if _CERT.fullmatch(value) is None:
            raise ValueError("certificate must be a decimal string with no leading zero")
        return value

    def __str__(self) -> str:
        return self.value


class LeadBank(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    company: HoldingCompany
    cert: Cert


class LeadLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ticker: str
    cert: str

    @model_validator(mode="after")
    def well_formed(self) -> LeadLink:
        if not _ticker_ok(self.ticker):
            raise ValueError("ticker must be uppercase ASCII letters and digits")
        Cert(value=self.cert)
        return self


class SnapshotMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    url: str
    fetched_at: str
    sha256: str
    bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def well_formed(self) -> SnapshotMetadata:
        if not self.url:
            raise ValueError("url is empty")
        try:
            datetime.strptime(self.fetched_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("fetched_at must be UTC seconds") from exc
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("sha256 must be lowercase hex")
        return self


class CallReportRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    cert: Cert
    repdte: str
    asset: int | float | None
    dep: int | float | None
    nimy: int | float | None
    eeffr: int | float | None
    netinc: int | float | None

    @field_validator("repdte")
    @classmethod
    def eight_digits(cls, value: str) -> str:
        if len(value) != 8 or not value.isdigit():
            raise ValueError("REPDTE must be eight digits")
        return value


class Financials(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    total: int = Field(ge=1)
    rows: tuple[CallReportRow, ...]

    @model_validator(mode="after")
    def total_matches_rows(self) -> Financials:
        if self.total != len(self.rows):
            raise ValueError("financials page is truncated")
        return self


@dataclass(frozen=True, slots=True)
class XbrlBody:
    url: str
    body: bytes
    fetched_at: str


@dataclass(frozen=True, slots=True)
class FinancialsBody:
    url: str
    body: bytes
    fetched_at: str
    rows: int


@dataclass(frozen=True, slots=True)
class LoadedCompany:
    lead: LeadBank
    xbrl: XbrlBody
    financials: FinancialsBody


@dataclass(frozen=True, slots=True)
class LoadedSnapshots:
    companies: tuple[LoadedCompany, ...]


@dataclass(frozen=True, slots=True)
class WrittenXbrl:
    ticker: str
    path: Path
    metadata_path: Path
    metadata: SnapshotMetadata


@dataclass(frozen=True, slots=True)
class WrittenFinancials:
    cert: Cert
    path: Path
    metadata_path: Path
    metadata: SnapshotMetadata
    rows: int


@dataclass(frozen=True, slots=True)
class WrittenCompany:
    lead: LeadBank
    xbrl: WrittenXbrl
    financials: WrittenFinancials


@dataclass(frozen=True, slots=True)
class ExtractReceipt:
    companies: tuple[WrittenCompany, ...]


def require_companyfacts(body: bytes, company: HoldingCompany) -> None:
    payload = _object(body, "companyfacts")
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise MetricsInputError("companyfacts facts was not an object")
    if _cik_int(payload.get("cik")) != int(company.cik):
        raise MetricsInputError(f"companyfacts cik is not {company.ticker}")


def require_financials(body: bytes, cert: Cert) -> Financials:
    parsed = _parse_financials(body)
    for row in parsed.rows:
        if row.cert != cert:
            raise MetricsInputError(f"financials CERT {row.cert} is not {cert}")
    return parsed


def trim_companyfacts(body: bytes) -> bytes:
    """Canonical companyfacts projection. trim(trim(body)) equals trim(body)."""
    payload = _object(body, "companyfacts")
    entity = payload.get("entityName")
    facts = payload.get("facts")
    if not isinstance(entity, str) or not isinstance(facts, dict):
        raise MetricsInputError("companyfacts body is not a facts document")
    kept_facts: dict[str, object] = {}
    for taxonomy in sorted(key for key in facts if isinstance(key, str)):
        concepts = facts[taxonomy]
        if not isinstance(concepts, dict):
            continue
        kept_concepts: dict[str, object] = {}
        for concept in sorted(key for key in concepts if isinstance(key, str)):
            kept_units = _trim_units(concepts[concept])
            if kept_units:
                kept_concepts[concept] = {"units": kept_units}
        if kept_concepts:
            kept_facts[taxonomy] = kept_concepts
    return _dump(
        {
            "cik": _cik10(payload.get("cik")),
            "entityName": entity,
            "facts": kept_facts,
        }
    )


def trim_financials(body: bytes) -> bytes:
    """Canonical call-report projection. trim(trim(body)) equals trim(body)."""
    parsed = _parse_financials(body)
    order = sorted(range(len(parsed.rows)), key=lambda index: (parsed.rows[index].repdte, index))
    data = [{"data": _emit_row(parsed.rows[index])} for index in order]
    return _dump({"meta": {"total": len(data)}, "data": data})


def metadata_text(meta: SnapshotMetadata) -> str:
    return json.dumps(meta.model_dump(), indent=2, sort_keys=True) + "\n"


def leads_text(links: tuple[LeadLink, ...]) -> str:
    payload = [link.model_dump() for link in links]
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _ticker_ok(ticker: str) -> bool:
    return ticker.isascii() and ticker.isalnum() and ticker == ticker.upper() and ticker != ""


class MetricsStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_run(self, loaded: LoadedSnapshots) -> ExtractReceipt:
        staged: list[_Staged] = []
        seen_tickers: set[str] = set()
        seen_certs: set[str] = set()
        for company in loaded.companies:
            ticker = company.lead.company.ticker
            cert = company.lead.cert
            if ticker in seen_tickers or str(cert) in seen_certs:
                raise MetricsInputError("duplicate ticker or certificate in one extract")
            seen_tickers.add(ticker)
            seen_certs.add(str(cert))
            xbrl_bytes = trim_companyfacts(company.xbrl.body)
            fin_bytes = trim_financials(company.financials.body)
            require_companyfacts(xbrl_bytes, company.lead.company)
            financials = require_financials(fin_bytes, cert)
            if financials.total != company.financials.rows:
                raise MetricsInputError(f"financials rows for {ticker} do not match the projection")
            staged.append(
                _Staged(
                    company=company,
                    xbrl_bytes=xbrl_bytes,
                    fin_bytes=fin_bytes,
                    xbrl_meta=_metadata(company.xbrl.url, company.xbrl.fetched_at, xbrl_bytes),
                    fin_meta=_metadata(
                        company.financials.url,
                        company.financials.fetched_at,
                        fin_bytes,
                    ),
                    rows=financials.total,
                )
            )
        written: list[WrittenCompany] = []
        links: list[LeadLink] = []
        for item in staged:
            ticker = item.company.lead.company.ticker
            cert = item.company.lead.cert
            xbrl_path = self.xbrl_path(ticker)
            fin_path = self.financials_path(cert)
            xbrl_meta_path = _sidecar(xbrl_path)
            fin_meta_path = _sidecar(fin_path)
            atomic_write_bytes(xbrl_path, item.xbrl_bytes)
            atomic_write_text(xbrl_meta_path, metadata_text(item.xbrl_meta))
            atomic_write_bytes(fin_path, item.fin_bytes)
            atomic_write_text(fin_meta_path, metadata_text(item.fin_meta))
            link = LeadLink(ticker=ticker, cert=str(cert))
            links.append(link)
            written.append(
                WrittenCompany(
                    lead=item.company.lead,
                    xbrl=WrittenXbrl(
                        ticker=ticker,
                        path=xbrl_path,
                        metadata_path=xbrl_meta_path,
                        metadata=item.xbrl_meta,
                    ),
                    financials=WrittenFinancials(
                        cert=cert,
                        path=fin_path,
                        metadata_path=fin_meta_path,
                        metadata=item.fin_meta,
                        rows=item.rows,
                    ),
                )
            )
        atomic_write_text(self._leads_path(), leads_text(tuple(links)))
        return ExtractReceipt(companies=tuple(written))

    def read_leads(self) -> tuple[LeadLink, ...]:
        path = self._leads_path()
        if not path.is_file():
            raise MetricsInputError("lead_banks.json is missing")
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MetricsInputError("lead_banks.json was not JSON") from exc
        if not isinstance(payload, list):
            raise MetricsInputError("lead_banks.json was not a list")
        links: list[LeadLink] = []
        for item in payload:
            try:
                link = LeadLink.model_validate(item)
            except ValidationError as exc:
                raise MetricsInputError("lead_banks.json row is invalid") from exc
            self._require_pair(link)
            links.append(link)
        return tuple(links)

    def xbrl_path(self, ticker: str) -> Path:
        if not _ticker_ok(ticker):
            raise MetricsInputError(f"refusing xbrl path for {ticker}")
        return self.root / "raw" / "xbrl" / f"{ticker}.json"

    def financials_path(self, cert: Cert) -> Path:
        return self.root / "raw" / "fdic" / "financials" / f"{cert}.json"

    def _leads_path(self) -> Path:
        return self.root / "raw" / "metrics" / "lead_banks.json"

    def _require_pair(self, link: LeadLink) -> None:
        cert = Cert(value=link.cert)
        paths = (self.xbrl_path(link.ticker), self.financials_path(cert))
        for payload_path in paths:
            meta_path = _sidecar(payload_path)
            if not payload_path.is_file() or not meta_path.is_file():
                raise MetricsInputError(f"missing snapshot for {link.ticker}")
            body = payload_path.read_bytes()
            try:
                meta = SnapshotMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
            except ValidationError as exc:
                raise MetricsInputError(f"invalid metadata for {payload_path.name}") from exc
            digest = hashlib.sha256(body).hexdigest()
            if meta.sha256 != digest or meta.bytes != len(body):
                raise MetricsInputError(f"sha256 does not match {payload_path.name}")


@dataclass(frozen=True, slots=True)
class _Staged:
    company: LoadedCompany
    xbrl_bytes: bytes
    fin_bytes: bytes
    xbrl_meta: SnapshotMetadata
    fin_meta: SnapshotMetadata
    rows: int


def _sidecar(path: Path) -> Path:
    return path.with_name(f"{path.stem}.metadata.json")


def _metadata(url: str, fetched_at: str, body: bytes) -> SnapshotMetadata:
    return SnapshotMetadata(
        url=url,
        fetched_at=fetched_at,
        sha256=hashlib.sha256(body).hexdigest(),
        bytes=len(body),
    )


def _object(body: bytes, label: str) -> dict[str, object]:
    try:
        payload: object = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MetricsInputError(f"{label} body was not JSON") from exc
    if not isinstance(payload, dict):
        raise MetricsInputError(f"{label} body was not an object")
    return payload


def _cik_int(value: object) -> int:
    if isinstance(value, bool):
        raise MetricsInputError("cik is not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise MetricsInputError("cik is not a number")


def _cik10(value: object) -> str:
    text = str(_cik_int(value))
    if len(text) > 10:
        raise MetricsInputError("cik is not 10 digits")
    return text.zfill(10)


def _trim_units(entry: object) -> dict[str, object]:
    if not isinstance(entry, dict):
        return {}
    units = entry.get("units")
    if not isinstance(units, dict):
        return {}
    kept: dict[str, object] = {}
    for unit in sorted(key for key in units if isinstance(key, str)):
        series = units[unit]
        if not isinstance(series, list):
            continue
        observations = [_observation(item) for item in series]
        kept_obs = [item for item in observations if item is not None]
        if not kept_obs:
            continue
        kept_obs.sort(key=_obs_key)
        kept[unit] = kept_obs
    return kept


def _observation(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict) or not _is_number(item.get("val")):
        return None
    return {key: item[key] for key in _OBS_KEYS if key in item}


def _obs_key(obs: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(obs.get(key, "")) for key in _OBS_SORT)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _parse_financials(body: bytes) -> Financials:
    payload = _object(body, "financials")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise MetricsInputError("financials meta was not an object")
    total = meta.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        raise MetricsInputError("financials meta.total was not an int")
    data = payload.get("data")
    if not isinstance(data, list):
        raise MetricsInputError("financials data was not a list")
    if total != len(data):
        raise MetricsInputError("financials page is truncated")
    if total < 1:
        raise MetricsInputError("financials page is empty")
    rows: list[CallReportRow] = []
    expected: Cert | None = None
    for item in data:
        raw = _row_dict(item)
        row = _call_row(raw)
        if expected is None:
            expected = row.cert
        elif row.cert != expected:
            raise MetricsInputError("financials page has a second certificate")
        rows.append(row)
    return Financials(total=total, rows=tuple(rows))


def _row_dict(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        inner = item.get("data")
        if isinstance(inner, dict):
            return inner
        return item
    raise MetricsInputError("financials row was not an object")


def _call_row(raw: dict[str, object]) -> CallReportRow:
    if "CERT" not in raw or "REPDTE" not in raw:
        raise MetricsInputError("financials row is missing CERT or REPDTE")
    cert = _parse_cert(raw.get("CERT"))
    return CallReportRow(
        cert=cert,
        repdte=_repdte(raw.get("REPDTE")),
        asset=_metric(raw, "ASSET"),
        dep=_metric(raw, "DEP"),
        nimy=_metric(raw, "NIMY"),
        eeffr=_metric(raw, "EEFFR"),
        netinc=_metric(raw, "NETINC"),
    )


def _parse_cert(value: object) -> Cert:
    if isinstance(value, bool):
        raise MetricsInputError("CERT rejects bool")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise MetricsInputError("CERT is not an integer")
        text = str(int(value))
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise MetricsInputError("CERT is missing")
    try:
        return Cert(value=text)
    except ValidationError as exc:
        raise MetricsInputError("CERT is not a decimal string") from exc


def _repdte(value: object) -> str:
    if isinstance(value, bool):
        raise MetricsInputError("REPDTE rejects bool")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise MetricsInputError("REPDTE must be eight digits")
    if len(text) != 8 or not text.isdigit():
        raise MetricsInputError("REPDTE must be eight digits")
    return text


def _metric(raw: dict[str, object], key: str) -> int | float | None:
    if key not in raw or raw[key] is None:
        return None
    return _number(raw[key], field=key)


def _number(value: object, *, field: str) -> int | float:
    if isinstance(value, bool):
        raise MetricsInputError(f"{field} rejects bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _finite(value, field)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            raise MetricsInputError(f"{field} is not a number")
        return _parse_number_text(text, field)
    raise MetricsInputError(f"{field} is not a number")


def _parse_number_text(text: str, field: str) -> int | float:
    try:
        if any(mark in text for mark in (".", "e", "E")):
            return _finite(float(text), field)
        return int(text)
    except ValueError as exc:
        raise MetricsInputError(f"{field} is not a number") from exc


def _finite(value: float, field: str) -> int | float:
    if not math.isfinite(value):
        raise MetricsInputError(f"{field} rejects non-finite")
    if value.is_integer():
        return int(value)
    return value


def _emit_row(row: CallReportRow) -> dict[str, object]:
    return {
        "CERT": int(str(row.cert)),
        "REPDTE": row.repdte,
        "ASSET": row.asset,
        "DEP": row.dep,
        "NIMY": row.nimy,
        "EEFFR": row.eeffr,
        "NETINC": row.netinc,
    }


def _dump(payload: object) -> bytes:
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")
