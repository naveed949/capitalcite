"""Citation policy for one fact row. The SQL backends do not import this module."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, assert_never

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.tokens import count_tokens
from capitalcite.ingest.client import filing_index_url
from capitalcite.metric_query.query import (
    EntityLevel,
    MetricCell,
    MetricName,
    MetricQuery,
    MetricQueryError,
    render_figure,
)
from capitalcite.retrieval.types import Hit

FACT_SQL = """
select
  f.ticker,
  f.quarter_id,
  f.entity_level,
  b.cik,
  f.assets_sec_accn,
  f.deposits_sec_accn,
  f.net_income_sec_accn,
  f.net_income_method,
  f.net_income_nine_month_sec_accn,
  f.fdic_repdte,
  f.fdic_source_url
from fct_bank_financials_quarterly as f
inner join dim_bank as b
  on f.bank_key = b.bank_key
where f.ticker = ?
  and f.quarter_id = ?
  and f.entity_level = ?
"""


class CiteKind(StrEnum):
    FDIC = "fdic"
    SEC_ASSETS = "sec_assets"
    SEC_DEPOSITS = "sec_deposits"
    SEC_NET_INCOME = "sec_net_income"


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["fdic", "sec"]
    doc_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    label: str = Field(min_length=1)
    accession: str = ""


class FactRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ticker: str
    quarter_id: date
    entity_level: EntityLevel
    cik: str | None
    assets_sec_accn: str | None
    deposits_sec_accn: str | None
    net_income_sec_accn: str | None
    net_income_method: Literal["reported_3m", "fy_minus_9m"] | None
    net_income_nine_month_sec_accn: str | None
    fdic_repdte: str | None
    fdic_source_url: str | None


class CitationBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sources: tuple[SourceRef, ...] = Field(min_length=1)


def cite_kind(metric: MetricName) -> CiteKind:
    """Exhaustive match.

    total_deposits, total_assets, net_income, nim, efficiency_ratio, and
    deposits_qoq_growth return FDIC. The three holding-company metrics
    return the SEC kind with the same stem.
    """
    match metric:
        case (
            MetricName.TOTAL_DEPOSITS
            | MetricName.TOTAL_ASSETS
            | MetricName.NET_INCOME
            | MetricName.NIM
            | MetricName.EFFICIENCY_RATIO
            | MetricName.DEPOSITS_QOQ_GROWTH
        ):
            return CiteKind.FDIC
        case MetricName.HOLDING_COMPANY_ASSETS:
            return CiteKind.SEC_ASSETS
        case MetricName.HOLDING_COMPANY_DEPOSITS:
            return CiteKind.SEC_DEPOSITS
        case MetricName.HOLDING_COMPANY_NET_INCOME:
            return CiteKind.SEC_NET_INCOME
        case _:
            assert_never(metric)


class CitationResolver:
    def __init__(self, warehouse: Path) -> None:
        self._warehouse = warehouse

    def resolve(self, cell: MetricCell) -> CitationBundle:
        """Read one fact row after the cell exists.

        The quote number comes from cell.value. The fact amount is ignored.
        deposits_qoq_growth cites the current FDIC row only.
        """
        if cell.value is None:
            raise MetricQueryError("metric cell is null")
        row = self._read(cell.query)
        kind = cite_kind(cell.query.metric)
        sources: tuple[SourceRef, ...]
        match kind:
            case CiteKind.FDIC:
                sources = (_fdic_source(row),)
            case CiteKind.SEC_ASSETS:
                sources = (_sec_source(row, row.assets_sec_accn),)
            case CiteKind.SEC_DEPOSITS:
                sources = (_sec_source(row, row.deposits_sec_accn),)
            case CiteKind.SEC_NET_INCOME:
                sources = _net_income_sources(row)
            case _:
                assert_never(kind)
        return CitationBundle(sources=sources)

    def _read(self, query: MetricQuery) -> FactRow:
        if not self._warehouse.is_file():
            raise MetricQueryError("warehouse is missing")
        connection = duckdb.connect(str(self._warehouse), read_only=True)
        try:
            try:
                fetched = connection.execute(
                    FACT_SQL,
                    [query.ticker, query.period, query.entity_level.value],
                ).fetchall()
            except duckdb.Error as exc:
                raise MetricQueryError("metric fact read failed") from exc
        finally:
            connection.close()
        if len(fetched) != 1:
            raise MetricQueryError("metric fact did not return one row")
        row = _fact_row(fetched[0])
        if (
            row.ticker != query.ticker
            or row.quarter_id != query.period
            or row.entity_level != query.entity_level
        ):
            raise MetricQueryError("metric fact row does not match the query")
        return row


def hits_for(cell: MetricCell, bundle: CitationBundle) -> tuple[Hit, ...]:
    """One Hit per SourceRef. The figure is render_figure(cell.value)."""
    if cell.value is None:
        raise MetricQueryError("metric cell is null")
    hits: list[Hit] = []
    for source in bundle.sources:
        text = _quote(cell, source)
        # The fake draft keeps every hit whose section is a metric name,
        # so both fy_minus_9m accessions reach the answer.
        chunk = Chunk(
            doc_id=source.doc_id,
            section=cell.query.metric.value,
            start=0,
            end=len(text),
            offset=0,
            source_url=source.source_url,
            text=text,
            token_count=count_tokens(text),
            chunker=ChunkerName.FIXED,
            header_prefix="",
            ticker=cell.query.ticker,
            period=cell.query.period.isoformat(),
            accession=source.accession,
        )
        hits.append(Hit(score=1.0, chunk=chunk))
    return tuple(hits)


def _quote(cell: MetricCell, source: SourceRef) -> str:
    if cell.value is None:
        raise MetricQueryError("metric cell is null")
    figure = render_figure(cell.value)
    query = cell.query
    kind = "FDIC" if source.kind == "fdic" else "SEC"
    text = (
        f"{query.metric.value} for {query.ticker} at {query.period.isoformat()} "
        f"is {figure}. Unit is {_unit(query.metric)}."
    )
    if query.metric in {MetricName.NIM, MetricName.EFFICIENCY_RATIO}:
        text += f" The figure is {figure}%."
    text += f" {kind} {source.label}. Source {source.source_url}"
    return text


def _unit(metric: MetricName) -> str:
    match metric:
        case MetricName.TOTAL_DEPOSITS | MetricName.TOTAL_ASSETS | MetricName.NET_INCOME:
            return "thousands of USD"
        case MetricName.NIM | MetricName.EFFICIENCY_RATIO:
            return "percent"
        case (
            MetricName.HOLDING_COMPANY_ASSETS
            | MetricName.HOLDING_COMPANY_DEPOSITS
            | MetricName.HOLDING_COMPANY_NET_INCOME
        ):
            return "whole USD"
        case MetricName.DEPOSITS_QOQ_GROWTH:
            return "a ratio"
        case _:
            assert_never(metric)


def _fdic_source(row: FactRow) -> SourceRef:
    repdte = _required(row.fdic_repdte)
    url = _required(row.fdic_source_url)
    return SourceRef(
        kind="fdic",
        doc_id=f"fdic:{repdte}",
        source_url=url,
        label=f"REPDTE {repdte}",
    )


def _sec_source(row: FactRow, accession: str | None) -> SourceRef:
    accn = _required(accession)
    cik = _required(row.cik)
    return SourceRef(
        kind="sec",
        doc_id=accn,
        source_url=filing_index_url(cik, accn),
        label=f"accession {accn}",
        accession=accn,
    )


def _net_income_sources(row: FactRow) -> tuple[SourceRef, ...]:
    if row.net_income_method is None:
        raise MetricQueryError("metric fact is missing a source")
    primary = _sec_source(row, row.net_income_sec_accn)
    if row.net_income_method == "reported_3m":
        return (primary,)
    nine_month = _sec_source(row, row.net_income_nine_month_sec_accn)
    return (primary, nine_month)


def _required(value: str | None) -> str:
    if value is None or not value.strip():
        raise MetricQueryError("metric fact is missing a source")
    return value


def _fact_row(raw: tuple[object, ...]) -> FactRow:
    if len(raw) != 11:
        raise MetricQueryError("metric fact row is missing")
    payload = {
        "ticker": raw[0],
        "quarter_id": _as_date(raw[1]),
        "entity_level": raw[2],
        "cik": _blank_none(raw[3]),
        "assets_sec_accn": _blank_none(raw[4]),
        "deposits_sec_accn": _blank_none(raw[5]),
        "net_income_sec_accn": _blank_none(raw[6]),
        "net_income_method": _blank_none(raw[7]),
        "net_income_nine_month_sec_accn": _blank_none(raw[8]),
        "fdic_repdte": _blank_none(raw[9]),
        "fdic_source_url": _blank_none(raw[10]),
    }
    try:
        return FactRow.model_validate(payload)
    except ValidationError as exc:
        raise MetricQueryError("metric fact row is missing") from exc


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise MetricQueryError("metric fact row is missing")


def _blank_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value
