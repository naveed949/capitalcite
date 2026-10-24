"""Pointer-only numeric gold. The jsonl stores a source row, never a figure."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from capitalcite.metric_query.query import (
    GroupBy,
    MetricName,
    MetricQuery,
    locked_level,
    parse_metric_query,
)

DEFAULT_METRICS_ITEMS = Path("evals/golden/metrics.jsonl")
DEFAULT_FIXTURE_ROOT = Path("tests/fixtures/metrics")
_REQUIRED_TICKERS = frozenset({"JPM", "BAC", "WFC", "C"})
_MIN_ITEMS = 15


class MetricGoldError(ValueError):
    """The jsonl or a pointer is not legal gold."""


class FdicField(StrEnum):
    ASSET = "ASSET"
    DEP = "DEP"
    NIMY = "NIMY"
    EEFFR = "EEFFR"
    NETINC = "NETINC"


class FdicPointer(BaseModel):
    """One Call Report row. field selects the rule, not a stored number."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["fdic"] = "fdic"
    cert: str = Field(pattern=r"^[0-9]+$")
    repdte: str = Field(pattern=r"^[0-9]{8}$")
    field: FdicField


class FdicGrowthPointer(BaseModel):
    """DEP on this REPDTE, scored as quarter-on-quarter growth."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["fdic_deposits_qoq"] = "fdic_deposits_qoq"
    cert: str = Field(pattern=r"^[0-9]+$")
    repdte: str = Field(pattern=r"^[0-9]{8}$")


class SecPointer(BaseModel):
    """One companyfacts point, or a concept missing from a real accession."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["sec"] = "sec"
    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9]{0,5}$")
    accn: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    concept: Literal["Assets", "Deposits", "NetIncomeLoss"]
    end: date
    unit: Literal["USD"] = "USD"


SourcePointer = Annotated[
    FdicPointer | FdicGrowthPointer | SecPointer,
    Field(discriminator="kind"),
]


class MetricGoldItem(BaseModel):
    """A question plus the row that answers it. No answer string, no figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^m-[0-9]{3}$")
    question: str = Field(min_length=1)
    source: SourcePointer


class ResolvedValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["value"] = "value"
    value: Decimal
    query: MetricQuery


class ResolvedNull(BaseModel):
    """The cited row exists and the rule yields SQL null. Zero is not this."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["null"] = "null"
    query: MetricQuery


Resolved = Annotated[ResolvedValue | ResolvedNull, Field(discriminator="kind")]

_FDIC_METRIC = {
    FdicField.ASSET: MetricName.TOTAL_ASSETS,
    FdicField.DEP: MetricName.TOTAL_DEPOSITS,
    FdicField.NETINC: MetricName.NET_INCOME,
    FdicField.NIMY: MetricName.NIM,
    FdicField.EEFFR: MetricName.EFFICIENCY_RATIO,
}
_SEC_METRIC = {
    "Assets": MetricName.HOLDING_COMPANY_ASSETS,
    "Deposits": MetricName.HOLDING_COMPANY_DEPOSITS,
    "NetIncomeLoss": MetricName.HOLDING_COMPANY_NET_INCOME,
}


def implied_query(pointer: SourcePointer, fixture_root: Path) -> MetricQuery:
    """Ticker from lead_banks.json or the SEC pointer. Period is the quarter-end."""
    banks = _lead_banks(fixture_root)
    if isinstance(pointer, SecPointer):
        if pointer.ticker not in banks.values():
            raise MetricGoldError("sec ticker is not a lead bank")
        ticker = pointer.ticker
        period = pointer.end
        metric = _SEC_METRIC[pointer.concept]
    else:
        found = banks.get(pointer.cert)
        if found is None:
            raise MetricGoldError("fdic cert is not a lead bank")
        ticker = found
        period = _repdte_date(pointer.repdte)
        if isinstance(pointer, FdicGrowthPointer):
            metric = MetricName.DEPOSITS_QOQ_GROWTH
        else:
            metric = _FDIC_METRIC[pointer.field]
    try:
        return MetricQuery(
            metric=metric,
            group_by=GroupBy.QUARTER_AND_TICKER,
            ticker=ticker,
            period=period,
            entity_level=locked_level(metric),
        )
    except ValueError as exc:
        raise MetricGoldError("pointer period is not a quarter-end") from exc


def resolve(pointer: SourcePointer, fixture_root: Path) -> Resolved:
    """Derive the cell from fixture JSON. Never opens DuckDB."""
    query = implied_query(pointer, fixture_root)
    if isinstance(pointer, FdicPointer):
        return _resolve_fdic(pointer, query, fixture_root)
    if isinstance(pointer, FdicGrowthPointer):
        return _resolve_growth(pointer, query, fixture_root)
    return _resolve_sec(pointer, query, fixture_root)


async def load_metric_gold(
    path: Path | None = None,
    *,
    fixture_root: Path | None = None,
) -> tuple[MetricGoldItem, ...]:
    """Parse, resolve, and reject a suite that cannot be scored."""
    target = path if path is not None else DEFAULT_METRICS_ITEMS
    root = fixture_root if fixture_root is not None else DEFAULT_FIXTURE_ROOT
    items = _parse_items(target)
    if len(items) < _MIN_ITEMS:
        raise MetricGoldError(f"metrics gold needs at least {_MIN_ITEMS} items")
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise MetricGoldError("metrics gold has a duplicate id")
    banks = _lead_banks(root)
    if set(banks.values()) != _REQUIRED_TICKERS or len(banks) != len(_REQUIRED_TICKERS):
        raise MetricGoldError("lead_banks.json tickers are not the four lead banks")
    resolved = [resolve(item.source, root) for item in items]
    if not any(isinstance(row, ResolvedValue) and row.value < 0 for row in resolved):
        raise MetricGoldError("metrics gold has no negative value")
    if not any(isinstance(row, ResolvedNull) for row in resolved):
        raise MetricGoldError("metrics gold has no null")
    if not any(isinstance(row, ResolvedValue) and row.value == 0 for row in resolved):
        raise MetricGoldError("metrics gold has no zero value")
    tickers = {row.query.ticker for row in resolved}
    if tickers != _REQUIRED_TICKERS:
        raise MetricGoldError("metrics gold does not cover the four lead banks")
    await _lock_planner(items, resolved)
    return tuple(items)


def _resolve_fdic(pointer: FdicPointer, query: MetricQuery, root: Path) -> Resolved:
    rows = _fdic_rows(root, pointer.cert)
    current = _field(rows, pointer.repdte, pointer.field)
    if pointer.field is not FdicField.NETINC:
        return ResolvedValue(value=current, query=query)
    # NETINC is year-to-date. Q1 is already the quarter. A later quarter
    # subtracts the prior quarter's YTD, and a gap inside the year is null.
    if query.period.month == 3:
        return ResolvedValue(value=current, query=query)
    prior = query.prior_quarter_end()
    if prior.year != query.period.year:
        return ResolvedNull(query=query)
    prior_row = rows.get(prior.strftime("%Y%m%d"))
    if prior_row is None or FdicField.NETINC not in prior_row:
        return ResolvedNull(query=query)
    return ResolvedValue(value=current - prior_row[FdicField.NETINC], query=query)


def _resolve_growth(pointer: FdicGrowthPointer, query: MetricQuery, root: Path) -> Resolved:
    rows = _fdic_rows(root, pointer.cert)
    current = _field(rows, pointer.repdte, FdicField.DEP)
    prior_row = rows.get(query.prior_quarter_end().strftime("%Y%m%d"))
    if prior_row is None or FdicField.DEP not in prior_row:
        return ResolvedNull(query=query)
    prior = prior_row[FdicField.DEP]
    if prior == 0:
        return ResolvedNull(query=query)
    return ResolvedValue(value=(current - prior) / prior, query=query)


def _resolve_sec(pointer: SecPointer, query: MetricQuery, root: Path) -> Resolved:
    concepts = _sec_concepts(root, pointer.ticker)
    end = pointer.end.isoformat()
    if pointer.concept == "Assets":
        hits = _matching(concepts.get("Assets", ()), pointer.accn, end)
        if len(hits) != 1:
            raise MetricGoldError("sec Assets point is missing")
        return ResolvedValue(value=hits[0], query=query)
    # A raw NetIncomeLoss or Deposits val is not a quarter. These fixtures
    # have no flow points, so a present concept is an oracle error, not a cell.
    named = _matching(concepts.get(pointer.concept, ()), pointer.accn, end)
    if named:
        raise MetricGoldError("sec flow derivation is not in the fixture oracle")
    if not any(_matching(points, pointer.accn, end) for points in concepts.values()):
        raise MetricGoldError("sec accession point is missing")
    return ResolvedNull(query=query)


def _parse_items(path: Path) -> list[MetricGoldItem]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MetricGoldError(f"metrics gold is unreadable: {path.name}") from exc
    items: list[MetricGoldItem] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            items.append(MetricGoldItem.model_validate_json(line))
        except ValidationError as exc:
            raise MetricGoldError("metrics gold row is invalid") from exc
    return items


async def _lock_planner(items: list[MetricGoldItem], resolved: list[Resolved]) -> None:
    from capitalcite.agent.models import FakeAgentModel

    model = FakeAgentModel()
    for item, row in zip(items, resolved, strict=True):
        decision = await model.plan(item.question, [], [])
        calls = decision.calls
        if decision.kind != "plan" or len(calls) != 1 or calls[0].name != "metric_query":
            raise MetricGoldError(f"{item.id} planner did not emit one metric_query")
        try:
            parsed = parse_metric_query(calls[0].args)
        except ValueError as exc:
            raise MetricGoldError(f"{item.id} planner metric_query did not parse") from exc
        if parsed != row.query:
            raise MetricGoldError(f"{item.id} planner metric_query is not the implied query")


def _lead_banks(root: Path) -> dict[str, str]:
    payload = _read_json(root / "lead_banks.json")
    if not isinstance(payload, list):
        raise MetricGoldError("lead_banks.json is not a list")
    found: dict[str, str] = {}
    for row in payload:
        body = _mapping(row, "lead_banks.json row is invalid")
        cert = body.get("cert")
        ticker = body.get("ticker")
        if not isinstance(cert, str) or not isinstance(ticker, str):
            raise MetricGoldError("lead_banks.json row is invalid")
        if cert in found:
            raise MetricGoldError("lead_banks.json has a duplicate cert")
        found[cert] = ticker
    return found


def _fdic_rows(root: Path, cert: str) -> dict[str, dict[FdicField, Decimal]]:
    payload = _read_json(root / "fdic" / f"{cert}.json")
    body = _mapping(payload, "fdic fixture is not an object")
    raw_rows = body.get("data")
    if not isinstance(raw_rows, list):
        raise MetricGoldError("fdic fixture has no rows")
    found: dict[str, dict[FdicField, Decimal]] = {}
    for raw in raw_rows:
        wrapper = _mapping(raw, "fdic fixture row is invalid")
        data = _mapping(wrapper.get("data"), "fdic fixture row is invalid")
        repdte = data.get("REPDTE")
        if not isinstance(repdte, str):
            raise MetricGoldError("fdic fixture row has no REPDTE")
        fields: dict[FdicField, Decimal] = {}
        for field in FdicField:
            value = data.get(field.value)
            if isinstance(value, Decimal):
                fields[field] = value
        found[repdte] = fields
    return found


def _field(rows: dict[str, dict[FdicField, Decimal]], repdte: str, field: FdicField) -> Decimal:
    row = rows.get(repdte)
    if row is None or field not in row:
        raise MetricGoldError(f"fdic {field.value} row is missing")
    return row[field]


def _sec_concepts(root: Path, ticker: str) -> dict[str, tuple[tuple[str, str, Decimal], ...]]:
    payload = _read_json(root / "xbrl" / f"{ticker}.json")
    body = _mapping(payload, "sec fixture is not an object")
    facts = _mapping(body.get("facts"), "sec fixture has no facts")
    gaap = _mapping(facts.get("us-gaap"), "sec fixture has no us-gaap facts")
    concepts: dict[str, tuple[tuple[str, str, Decimal], ...]] = {}
    for name, raw in gaap.items():
        concept = _mapping(raw, "sec concept is invalid")
        units = _mapping(concept.get("units"), "sec concept has no units")
        points = units.get("USD")
        if not isinstance(points, list):
            concepts[name] = ()
            continue
        parsed: list[tuple[str, str, Decimal]] = []
        for point in points:
            row = _mapping(point, "sec point is invalid")
            accn = row.get("accn")
            end = row.get("end")
            val = row.get("val")
            if (
                not isinstance(accn, str)
                or not isinstance(end, str)
                or not isinstance(val, Decimal)
            ):
                raise MetricGoldError("sec point is invalid")
            parsed.append((accn, end, val))
        concepts[name] = tuple(parsed)
    return concepts


def _matching(
    points: tuple[tuple[str, str, Decimal], ...],
    accn: str,
    end: str,
) -> tuple[Decimal, ...]:
    return tuple(
        val for point_accn, point_end, val in points if point_accn == accn and point_end == end
    )


def _repdte_date(repdte: str) -> date:
    try:
        return date(int(repdte[0:4]), int(repdte[4:6]), int(repdte[6:8]))
    except ValueError as exc:
        raise MetricGoldError("pointer period is not a quarter-end") from exc


def _read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle, parse_float=Decimal, parse_int=Decimal)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricGoldError(f"fixture {path.name} is unreadable") from exc


def _mapping(value: object, rule: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MetricGoldError(rule)
    found: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise MetricGoldError(rule)
        found[key] = item
    return found


__all__ = [
    "DEFAULT_FIXTURE_ROOT",
    "DEFAULT_METRICS_ITEMS",
    "FdicField",
    "FdicGrowthPointer",
    "FdicPointer",
    "MetricGoldError",
    "MetricGoldItem",
    "Resolved",
    "ResolvedNull",
    "ResolvedValue",
    "SecPointer",
    "SourcePointer",
    "implied_query",
    "load_metric_gold",
    "resolve",
]
