from __future__ import annotations

import calendar
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "dbt" / "models" / "semantic" / "bank_financials.yml"
ENTITY_LEVELS = frozenset({"bank_subsidiary", "holding_company"})
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SIMPLE_FILTER = re.compile(
    r"^\{\{\s*Dimension\('bank__entity_level'\)\s*\}\}\s*=\s*'(bank_subsidiary|holding_company)'$"
)
QUERY_ROW = re.compile(
    r"^(?P<quarter>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+"
    r"(?P<ticker>[A-Za-z0-9.]+)\s+"
    r"(?P<deposits>\S+)"
    r"(?:\s+(?P<growth>\S+))?\s*$"
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


def committed_mf_stdout_path(repo: Path) -> Path:
    """Return the golden named for HEAD or the nearest ancestor.

    A shallow checkout only has HEAD in rev-list. The sole committed
    golden is then the contract.
    """
    directory = repo / "evals" / "results" / "metrics"
    listing = subprocess.run(
        ["git", "rev-list", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    for sha in listing.stdout.split():
        short = subprocess.run(
            ["git", "rev-parse", "--short=12", sha],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        path = directory / f"{short}-mf-query.txt"
        if path.is_file():
            return path
    files = sorted(directory.glob("*-mf-query.txt"))
    if len(files) == 1:
        return files[0]
    raise AssertionError("no committed mf query stdout under evals/results/metrics")


def _require_ci_target() -> None:
    if os.environ.get("DBT_TARGET") != "ci":
        raise AssertionError("DBT_TARGET must be ci before mf query")


def _strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def _run_required_query() -> str:
    _require_ci_target()
    completed = subprocess.run(
        REQUIRED_MF_QUERY,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return _strip_ansi(completed.stdout)


@pytest.fixture(scope="module")
def mf_query_stdout() -> str:
    return _run_required_query()


@pytest.mark.mf
def test_mf_query_matches_committed_stdout(mf_query_stdout: str) -> None:
    expected = committed_mf_stdout_path(ROOT).read_text(encoding="utf-8")
    assert mf_query_stdout == expected


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
    for line in stdout.splitlines():
        match = QUERY_ROW.match(line)
        if match is None or match.group("growth") is None:
            continue
        quarter_start = date.fromisoformat(match.group("quarter")[:10])
        return (
            match.group("ticker"),
            _quarter_end(quarter_start),
            match.group("deposits"),
            match.group("growth"),
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
    assert printed_deposits == format(this_amount, "g")
    assert printed_growth == format((this_amount - prior_amount) / prior_amount, "g")
