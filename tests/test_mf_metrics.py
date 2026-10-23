from __future__ import annotations

import calendar
import csv
import io
import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "dbt" / "models" / "semantic" / "bank_financials.yml"
GOLDEN = ROOT / "evals" / "results" / "metrics" / "mf-query.csv"
ENTITY_LEVELS = frozenset({"bank_subsidiary", "holding_company"})
RATE_METRICS = frozenset({"nim", "efficiency_ratio"})
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SIMPLE_FILTER = re.compile(
    r"^\{\{\s*Dimension\('bank__entity_level'\)\s*\}\}\s*=\s*'(bank_subsidiary|holding_company)'$"
)

REQUIRED_MF_QUERY: tuple[str, ...] = (
    "uv",
    "run",
    "--group",
    "metrics",
    "mf",
    "query",
    "--quiet",
    "--order",
    "metric_time__quarter,bank__ticker",
    "--metrics",
    "total_deposits,deposits_qoq_growth",
    "--group-by",
    "metric_time__quarter,bank__ticker",
    "--where",
    "{{ Dimension('bank__entity_level') }} = 'bank_subsidiary'",
    "--decimals",
    "10",
)


def _load_catalog() -> dict[str, Any]:
    loaded = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise AssertionError("semantic catalog is not a mapping")
    return loaded


def _mapping_list(catalog: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = catalog.get(key)
    if not isinstance(raw, list):
        raise AssertionError(f"{key} is missing")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AssertionError(f"{key} entry is not a mapping")
        rows.append(item)
    return rows


def test_simple_metrics_lock_entity_level() -> None:
    catalog = _load_catalog()
    measures = {
        str(measure["name"])
        for model in _mapping_list(catalog, "semantic_models")
        for measure in _mapping_list(model, "measures")
    }
    metrics = _mapping_list(catalog, "metrics")
    simple = [metric for metric in metrics if metric.get("type") == "simple"]
    assert simple, "catalog has no simple metrics"
    assert not any(metric.get("type") == "ratio" for metric in metrics)

    for metric in simple:
        name = str(metric["name"])
        assert name not in measures
        match = SIMPLE_FILTER.fullmatch(str(metric.get("filter", "")).strip())
        assert match is not None, name
        assert match.group(1) in ENTITY_LEVELS

    growth = next(metric for metric in metrics if metric["name"] == "deposits_qoq_growth")
    assert growth["type"] == "derived"
    assert "filter" not in growth
    inputs = growth["type_params"]["metrics"]
    assert [item["name"] for item in inputs] == ["total_deposits", "total_deposits"]


def test_rate_saved_queries_group_by_ticker() -> None:
    catalog = _load_catalog()
    rate_queries = 0
    for query in _mapping_list(catalog, "saved_queries"):
        metrics = {str(name) for name in query["query_params"]["metrics"]}
        if not metrics & RATE_METRICS:
            continue
        rate_queries += 1
        groups = [str(item) for item in query["query_params"]["group_by"]]
        assert any("bank__ticker" in item for item in groups), query["name"]
    assert rate_queries, "no saved query includes nim or efficiency_ratio"


def _csv_without_null_deposits(text: str) -> str:
    reader = csv.DictReader(io.StringIO(text.replace("\r\n", "\n")))
    if reader.fieldnames is None:
        raise AssertionError("mf csv has no header")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in reader:
        if (row.get("total_deposits") or "").strip():
            writer.writerow(row)
    return buffer.getvalue()


def _require_ci_target() -> None:
    if os.environ.get("DBT_TARGET") != "ci":
        raise AssertionError("DBT_TARGET must be ci before mf query")


def _strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def _run_required_query() -> str:
    _require_ci_target()
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
        csv_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [*REQUIRED_MF_QUERY, "--csv", str(csv_path)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        raw = _strip_ansi(csv_path.read_text(encoding="utf-8"))
    finally:
        csv_path.unlink(missing_ok=True)
    return _csv_without_null_deposits(raw)


@pytest.fixture(scope="module")
def mf_query_stdout() -> str:
    return _run_required_query()


@pytest.mark.mf
def test_mf_query_matches_committed_stdout(mf_query_stdout: str) -> None:
    expected = GOLDEN.read_text(encoding="utf-8")
    assert mf_query_stdout == expected
    assert ",,\n" not in mf_query_stdout


def _quarter_end(quarter_start: date) -> date:
    month = quarter_start.month + 2
    year = quarter_start.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _prior_quarter_end(quarter_end: date) -> date:
    month = quarter_end.month - 3
    year = quarter_end.year
    if month <= 0:
        month += 12
        year -= 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _first_growth_row(stdout: str) -> tuple[str, date, str, str]:
    reader = csv.DictReader(io.StringIO(stdout))
    for row in reader:
        growth = (row.get("deposits_qoq_growth") or "").strip()
        deposits = (row.get("total_deposits") or "").strip()
        if not growth or not deposits:
            continue
        quarter_start = date.fromisoformat(row["metric_time__quarter"][:10])
        return (
            row["bank__ticker"],
            _quarter_end(quarter_start),
            deposits,
            growth,
        )
    raise AssertionError("required query has no deposits_qoq_growth cell")


@pytest.mark.mf
def test_mf_growth_cell_matches_adjacent_deposits(mf_query_stdout: str) -> None:
    import duckdb

    ticker, this_end, printed_deposits, printed_growth = _first_growth_row(mf_query_stdout)
    prior_end = _prior_quarter_end(this_end)
    project = Path(os.environ.get("DBT_PROJECT_DIR", "dbt"))
    if not project.is_absolute():
        project = ROOT / project
    database = project / "target" / "ci.duckdb"
    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows = connection.execute(
            """
            select quarter_id, deposits_usd_thousands
            from fct_bank_financials_quarterly
            where entity_level = 'bank_subsidiary'
              and ticker = ?
              and quarter_id in (?, ?)
            order by quarter_id
            """,
            [ticker, prior_end, this_end],
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 2
    prior_amount = float(rows[0][1])
    this_amount = float(rows[1][1])
    assert prior_amount != 0
    assert float(printed_deposits) == this_amount
    assert float(printed_growth) == (this_amount - prior_amount) / prior_amount
