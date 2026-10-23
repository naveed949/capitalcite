"""MetricFlow SQL has one author. Live execution and the catalog compiler both call mf_argv."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
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
    """Ticker, quarter start, and entity_level, in that order.

    Jinja Dimension and TimeDimension calls are fixed text. The ticker
    pattern excludes quotes. entity_level is the enum value.
    """
    ticker = query.ticker
    quarter = query.quarter_start().isoformat()
    level = query.entity_level.value
    return (
        "{{ Dimension('bank__ticker') }} = '" + ticker + "' AND "
        "{{ TimeDimension('metric_time', 'quarter') }} = '" + quarter + "' AND "
        "{{ Dimension('bank__entity_level') }} = '" + level + "'"
    )


def mf_argv(
    query: MetricQuery,
    *,
    mode: _MfMode,
    csv_path: Path | None = None,
) -> list[str]:
    """uv run --group metrics mf query --quiet --metrics --group-by --where --decimals 10.

    Then --csv or --explain. No --target. DBT_TARGET is in the child environment.
    """
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
        """One entry per metric. Duplicate keys raise MetricQueryError."""
        found: dict[tuple[MetricName, GroupBy], CatalogEntry] = {}
        for entry in self.entries:
            key = (entry.metric, entry.group_by)
            if key in found:
                raise MetricQueryError("metric catalog has a duplicate entry")
            found[key] = entry
        return found


class CatalogSql:
    """Executes one precompiled statement. Opens DuckDB read_only per execute and closes it."""

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
                rows = connection.execute(statement, params).fetchall()
            except duckdb.Error as exc:
                raise MetricQueryError("metric query failed") from exc
        finally:
            connection.close()
        if len(rows) != 1 or len(rows[0]) != 1:
            raise MetricQueryError("metric query did not return one row")
        return MetricCell(query=query, value=_decimal_or_none(rows[0][0]))


def compile_catalog(path: Path, *, cwd: Path, env: Mapping[str, str]) -> None:
    """One explain per MetricName, using SENTINEL_TICKER and SENTINEL_QUARTER_END.

    parameterize replaces each quoted 'ZZQZZQ' and each quoted '1999-10-01'
    with ?, consuming the quotes, left to right, and records param_order.
    Zero quoted occurrences of either sentinel fail the compile. A leftover
    bare sentinel fails the compile. Any occurrence of 1999-12-31 fails the
    compile. A second statement fails the compile. semantic_sha256 is the
    sha256 of bank_financials.yml. The request process does not call this.
    """
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
    """Parse boundary for catalog.json. Extra keys fail. Version must be 1."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricQueryError("metric catalog is unreadable") from exc
    try:
        return Catalog.model_validate(payload)
    except ValidationError as exc:
        raise MetricQueryError("metric catalog is invalid") from exc


def parameterize(sql: str) -> tuple[str, tuple[ParamName, ...]]:
    """Replace quoted 'ZZQZZQ' and quoted '1999-10-01' only.

    Each match becomes a bare ? and one param_order entry.
    """
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
    """Strip ANSI. Keep from the first SELECT or WITH line through the end.

    Fail if that slice does not contain both sentinels.
    """
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
    return f"select cast({metric.value} as varchar) from ({inner}) as metric_cell"


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
