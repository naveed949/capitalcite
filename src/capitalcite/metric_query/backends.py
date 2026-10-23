from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, assert_never

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from capitalcite.atomic import atomic_write_text
from capitalcite.metric_query.query import (
    EntityLevel,
    GroupBy,
    MetricCell,
    MetricName,
    MetricQuery,
    MetricQueryError,
    locked_level,
)

SENTINEL_TICKER = "ZZQZZQ"
SENTINEL_QUARTER_END = date(1999, 12, 31)
_SENTINEL_QUARTER_START = "1999-10-01"
_QUOTED_SENTINEL = re.compile(rf"'({SENTINEL_TICKER}|{_SENTINEL_QUARTER_START})'")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_METRIC_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
ParamName = Literal["ticker", "quarter_start"]
_MfMode = Literal["csv", "explain"]


def where_clause(query: MetricQuery) -> str:
    """deposits_qoq_growth keeps the quarter literal behind OR 1 = 1.

    MetricFlow applies where before the offset, so a real time equality
    removes the prior quarter and the growth cell is null. Execute keeps
    the row whose metric_time__quarter is quarter_start.
    """
    ticker = query.ticker
    quarter = query.quarter_start().isoformat()
    level = query.entity_level.value
    ticker_eq = "{{ Dimension('bank__ticker') }} = '" + ticker + "'"
    time_eq = "{{ TimeDimension('metric_time', 'quarter') }} = '" + quarter + "'"
    level_eq = "{{ Dimension('bank__entity_level') }} = '" + level + "'"
    if query.metric is MetricName.DEPOSITS_QOQ_GROWTH:
        return f"{ticker_eq} AND ({time_eq} OR 1 = 1) AND {level_eq}"
    return f"{ticker_eq} AND {time_eq} AND {level_eq}"


def mf_argv(
    query: MetricQuery,
    *,
    mode: _MfMode,
    csv_path: Path | None = None,
) -> list[str]:
    argv = [
        "uv",
        "run",
        "--group",
        "metrics",
        "mf",
        "query",
        "--quiet",
        "--metrics",
        query.metric.value,
        "--group-by",
        query.group_by.value,
        "--where",
        where_clause(query),
        "--decimals",
        "10",
    ]
    if mode == "csv":
        if csv_path is None:
            raise MetricQueryError("metric query failed")
        argv.extend(["--csv", str(csv_path)])
        return argv
    if mode == "explain":
        argv.append("--explain")
        return argv
    assert_never(mode)


class CatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    metric: MetricName
    group_by: GroupBy
    entity_level: EntityLevel
    sql: str = Field(min_length=1)
    param_order: tuple[ParamName, ...]


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1]
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duckdb: Literal["2.0.0"]
    entries: tuple[CatalogEntry, ...]

    def by_key(self) -> dict[tuple[MetricName, GroupBy], CatalogEntry]:
        found: dict[tuple[MetricName, GroupBy], CatalogEntry] = {}
        for entry in self.entries:
            key = (entry.metric, entry.group_by)
            if key in found:
                raise MetricQueryError("metric catalog has a duplicate entry")
            found[key] = entry
        return found


class LiveMetricFlow:
    def __init__(self, *, cwd: Path, env: Mapping[str, str]) -> None:
        self._cwd = cwd
        self._env = dict(env)

    def execute(self, query: MetricQuery) -> MetricCell:
        with tempfile.NamedTemporaryFile(
            prefix="metric-query-", suffix=".csv", delete=False
        ) as handle:
            csv_path = Path(handle.name)
        try:
            completed = subprocess.run(
                mf_argv(query, mode="csv", csv_path=csv_path),
                cwd=self._cwd,
                env=dict(self._env),
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise MetricQueryError("metric query failed")
            raw = csv_path.read_text(encoding="utf-8")
        finally:
            csv_path.unlink(missing_ok=True)
        return _cell_from_rows(_csv_rows(raw), query)


class CatalogSql:
    def __init__(self, catalog: Catalog, warehouse: Path) -> None:
        self._by_key = catalog.by_key()
        self._warehouse = warehouse

    def execute(self, query: MetricQuery) -> MetricCell:
        try:
            entry = self._by_key[(query.metric, query.group_by)]
        except KeyError as exc:
            raise MetricQueryError("metric catalog has no entry") from exc
        if entry.entity_level != query.entity_level:
            raise MetricQueryError("metric catalog entity level does not match")
        params = [_bind(query, name) for name in entry.param_order]
        if not self._warehouse.is_file():
            raise MetricQueryError("warehouse is missing")
        statement = _cast_metric(entry.sql, query.metric)
        connection = duckdb.connect(str(self._warehouse), read_only=True)
        try:
            try:
                fetched = connection.execute(statement, params).fetchall()
            except duckdb.Error as exc:
                raise MetricQueryError("metric query failed") from exc
        finally:
            connection.close()
        rows = [
            {
                "bank__ticker": "" if row[0] is None else str(row[0]),
                "metric_time__quarter": "" if row[1] is None else str(row[1]),
                query.metric.value: "" if row[2] is None else str(row[2]),
            }
            for row in fetched
        ]
        return _cell_from_rows(rows, query)


def compile_catalog(path: Path, *, cwd: Path, env: Mapping[str, str]) -> None:
    digest = _semantic_sha256(cwd)
    entries: list[CatalogEntry] = []
    for metric in MetricName:
        query = MetricQuery(
            metric=metric,
            group_by=GroupBy.QUARTER_AND_TICKER,
            ticker=SENTINEL_TICKER,
            period=SENTINEL_QUARTER_END,
            entity_level=locked_level(metric),
        )
        completed = subprocess.run(
            mf_argv(query, mode="explain"),
            cwd=cwd,
            env=dict(env),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise MetricQueryError("metric explain failed")
        sql = sql_from_explain(completed.stdout)
        parameterized, order = _checked_sql(sql)
        entries.append(
            CatalogEntry(
                metric=metric,
                group_by=query.group_by,
                entity_level=query.entity_level,
                sql=parameterized,
                param_order=order,
            )
        )
    catalog = Catalog(version=1, semantic_sha256=digest, duckdb="2.0.0", entries=tuple(entries))
    atomic_write_text(path, _catalog_text(catalog))


def load_catalog(path: Path) -> Catalog:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricQueryError("metric catalog is unreadable") from exc
    try:
        return Catalog.model_validate(payload)
    except ValidationError as exc:
        raise MetricQueryError("metric catalog is invalid") from exc


def parameterize(sql: str) -> tuple[str, tuple[ParamName, ...]]:
    order: list[ParamName] = []

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == SENTINEL_TICKER:
            order.append("ticker")
        else:
            order.append("quarter_start")
        return "?"

    return _QUOTED_SENTINEL.sub(replace, sql), tuple(order)


def sql_from_explain(stdout: str) -> str:
    text = _ANSI.sub("", stdout)
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("SELECT") or stripped.startswith("WITH"):
            start = index
            break
    if start is None:
        raise MetricQueryError("metric explain did not return sql")
    sql = "\n".join(lines[start:]).strip()
    if SENTINEL_TICKER not in sql or _SENTINEL_QUARTER_START not in sql:
        raise MetricQueryError("metric explain sql is missing a sentinel")
    return sql


def _checked_sql(sql: str) -> tuple[str, tuple[ParamName, ...]]:
    if "1999-12-31" in sql:
        raise MetricQueryError("metric sql includes the fact grain")
    rewritten, order = parameterize(sql)
    if "ticker" not in order or "quarter_start" not in order:
        raise MetricQueryError("metric sql is missing a sentinel")
    if SENTINEL_TICKER in rewritten or _SENTINEL_QUARTER_START in rewritten:
        raise MetricQueryError("metric sql has a bare sentinel")
    if _more_than_one_statement(rewritten):
        raise MetricQueryError("metric sql has more than one statement")
    return rewritten, order


def _more_than_one_statement(sql: str) -> bool:
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return ";" in body


def _semantic_sha256(cwd: Path) -> str:
    path = cwd / "dbt" / "models" / "semantic" / "bank_financials.yml"
    if not path.is_file():
        raise MetricQueryError("semantic model is missing")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_text(catalog: Catalog) -> str:
    payload = catalog.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _bind(query: MetricQuery, name: ParamName) -> str:
    if name == "ticker":
        return query.ticker
    if name == "quarter_start":
        return query.quarter_start().isoformat()
    assert_never(name)


def _cast_metric(sql: str, metric: MetricName) -> str:
    if _METRIC_IDENT.fullmatch(metric.value) is None:
        raise MetricQueryError("metric query failed")
    inner = sql.strip().rstrip(";").strip()
    return (
        "select cast(bank__ticker as varchar), "
        "cast(metric_time__quarter as varchar), "
        f"cast({metric.value} as varchar) from ({inner}) as metric_cell"
    )


def _csv_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(_ANSI.sub("", text).replace("\r\n", "\n")))
    return list(reader)


def _cell_from_rows(rows: list[dict[str, str]], query: MetricQuery) -> MetricCell:
    """Growth SQL also returns other quarters, because the time predicate cannot
    filter inputs without erasing the prior quarter.
    """
    matched: list[str | None] = []
    for row in rows:
        ticker = (row.get("bank__ticker") or "").strip()
        quarter_raw = (row.get("metric_time__quarter") or "").strip()
        if not quarter_raw:
            raise MetricQueryError("metric query returned an unexpected quarter")
        try:
            quarter = date.fromisoformat(quarter_raw[:10])
        except ValueError as exc:
            raise MetricQueryError("metric query returned an unexpected quarter") from exc
        if quarter != query.quarter_start():
            continue
        if ticker != query.ticker:
            raise MetricQueryError("metric query returned an unexpected ticker")
        raw_value = row.get(query.metric.value)
        matched.append(None if raw_value is None else str(raw_value))
    if len(matched) != 1:
        raise MetricQueryError("metric query did not return one row")
    return MetricCell(query=query, value=_decimal_or_none(matched[0]))


def _decimal_or_none(raw: object) -> Decimal | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise MetricQueryError("metric query failed") from exc
