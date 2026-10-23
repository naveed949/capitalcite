"""metric_time__quarter is quarter_start(), the label mf prints."""

from __future__ import annotations

import calendar
import math
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, assert_never

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_QUARTER_START_MONTH = {3: 1, 6: 4, 9: 7, 12: 10}
_FIGURE_QUANTUM = Decimal("0.0000000001")
_WIRE_FIELDS = frozenset({"metric", "group_by", "ticker", "period", "entity_level"})


class MetricName(StrEnum):
    TOTAL_DEPOSITS = "total_deposits"
    TOTAL_ASSETS = "total_assets"
    NET_INCOME = "net_income"
    NIM = "nim"
    EFFICIENCY_RATIO = "efficiency_ratio"
    HOLDING_COMPANY_ASSETS = "holding_company_assets"
    HOLDING_COMPANY_DEPOSITS = "holding_company_deposits"
    HOLDING_COMPANY_NET_INCOME = "holding_company_net_income"
    DEPOSITS_QOQ_GROWTH = "deposits_qoq_growth"


class GroupBy(StrEnum):
    QUARTER_AND_TICKER = "metric_time__quarter,bank__ticker"


class EntityLevel(StrEnum):
    BANK_SUBSIDIARY = "bank_subsidiary"
    HOLDING_COMPANY = "holding_company"


class MetricQueryError(Exception):
    """Safe to copy onto ToolResult.detail."""


class MetricQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    metric: MetricName
    group_by: GroupBy
    ticker: str = Field(pattern=r"^[A-Z][A-Z0-9]{0,5}$")
    period: date
    entity_level: EntityLevel

    @model_validator(mode="after")
    def one_quarter_end(self) -> MetricQuery:
        if not _is_quarter_end(self.period):
            raise ValueError("period is not a quarter-end")
        if self.entity_level != locked_level(self.metric):
            raise ValueError("entity_level does not match the metric")
        return self

    def quarter_start(self) -> date:
        return date(self.period.year, _QUARTER_START_MONTH[self.period.month], 1)


class MetricCell(BaseModel):
    """The number for one MetricQuery. value None is a SQL null, and it is still one cell."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    query: MetricQuery
    value: Decimal | None


class QueryBackend(Protocol):
    def execute(self, query: MetricQuery) -> MetricCell:
        """Return the one cell.

        Raise MetricQueryError on transport failure or on a row count other
        than 1. The message must not include SQL, a warehouse path, or the
        rendered figure.
        """
        ...


def locked_level(metric: MetricName) -> EntityLevel:
    match metric:
        case (
            MetricName.TOTAL_DEPOSITS
            | MetricName.TOTAL_ASSETS
            | MetricName.NET_INCOME
            | MetricName.NIM
            | MetricName.EFFICIENCY_RATIO
        ):
            return EntityLevel.BANK_SUBSIDIARY
        case (
            MetricName.HOLDING_COMPANY_ASSETS
            | MetricName.HOLDING_COMPANY_DEPOSITS
            | MetricName.HOLDING_COMPANY_NET_INCOME
        ):
            return EntityLevel.HOLDING_COMPANY
        case MetricName.DEPOSITS_QOQ_GROWTH:
            return locked_level(MetricName.TOTAL_DEPOSITS)
        case _:
            assert_never(metric)


def parse_metric_query(args: Mapping[str, object]) -> MetricQuery:
    """Wire boundary.

    Uppercase ticker. Fill entity_level from locked_level when the key is
    missing. Reject a present entity_level that differs. Reject a period
    that is not a quarter-end. Reject unknown keys. Raise ValueError.
    """
    if not isinstance(args, Mapping):
        raise ValueError("metric query args are not an object")
    raw = dict(args)
    if set(raw) - _WIRE_FIELDS:
        raise ValueError("unknown metric query field")
    metric_raw = raw.get("metric")
    if not isinstance(metric_raw, str):
        raise ValueError("unknown metric")
    try:
        metric = MetricName(metric_raw)
    except ValueError as exc:
        raise ValueError("unknown metric") from exc
    locked = locked_level(metric)
    if "entity_level" not in raw or raw["entity_level"] is None:
        raw["entity_level"] = locked.value
    else:
        supplied = _entity_level(raw["entity_level"])
        if supplied != locked:
            raise ValueError("entity_level does not match the metric")
        raw["entity_level"] = supplied.value
    period = _period(raw.get("period"))
    if not _is_quarter_end(period):
        raise ValueError("period is not a quarter-end")
    raw["period"] = period
    ticker = raw.get("ticker")
    if isinstance(ticker, str):
        raw["ticker"] = ticker.upper()
    try:
        return MetricQuery.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("invalid metric query") from exc


def render_figure(value: Decimal) -> str:
    """Plain decimal text, no exponent, no thousands separators, no scale word.

    Quantize to 10 decimal places to match mf --decimals 10, then strip
    trailing zeros. The hit quote contains this string.
    """
    quantized = value.quantize(_FIGURE_QUANTUM)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def cells_equal(left: MetricCell, right: MetricCell) -> bool:
    if left.query != right.query:
        return False
    if left.value is None or right.value is None:
        return left.value is None and right.value is None
    return math.isclose(float(left.value), float(right.value), rel_tol=1e-12)


def _is_quarter_end(value: date) -> bool:
    if value.month not in _QUARTER_START_MONTH:
        return False
    return value.day == calendar.monthrange(value.year, value.month)[1]


def _period(value: object) -> date:
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("period is not a quarter-end") from exc
        if parsed.__class__ is not date:
            raise ValueError("period is not a quarter-end")
        return parsed
    if isinstance(value, date) and value.__class__ is date:
        return value
    raise ValueError("period is not a quarter-end")


def _entity_level(value: object) -> EntityLevel:
    if not isinstance(value, str):
        raise ValueError("unknown entity_level")
    try:
        return EntityLevel(value)
    except ValueError as exc:
        raise ValueError("unknown entity_level") from exc
