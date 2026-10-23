from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from capitalcite.gate.policy import BrokenPolicy, load_policy
from capitalcite.metric_query import MetricCell, cells_equal, parse_metric_query
from capitalcite.metric_query.cite import CiteKind, cite_kind
from capitalcite.metric_query.query import (
    EntityLevel,
    MetricName,
    locked_level,
    render_figure,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "dbt" / "models" / "semantic" / "bank_financials.yml"
TOOLS = ROOT / "src" / "capitalcite" / "gate" / "tools.json"
SIMPLE_FILTER = re.compile(
    r"^\{\{\s*Dimension\('bank__entity_level'\)\s*\}\}\s*=\s*'(bank_subsidiary|holding_company)'$"
)
_GROUP = "metric_time__quarter,bank__ticker"


def _wire(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "metric": "total_deposits",
        "group_by": _GROUP,
        "ticker": "jpm",
        "period": "2024-12-31",
    }
    args.update(overrides)
    return args


def test_metric_names_match_yaml_and_gate() -> None:
    loaded = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    yaml_names = {str(item["name"]) for item in loaded["metrics"]}
    enum_names = {metric.value for metric in MetricName}
    gate = json.loads(TOOLS.read_text(encoding="utf-8"))
    gate_names = set(gate["tools"]["metric_query"]["args"]["properties"]["metric"]["enum"])
    group_by = gate["tools"]["metric_query"]["args"]["properties"]["group_by"]["enum"]
    assert yaml_names == enum_names == gate_names
    assert group_by == [_GROUP]


def test_gate_metric_query_rule_loads() -> None:
    loaded = load_policy(None)
    assert not isinstance(loaded, BrokenPolicy)
    rule = loaded.tools["metric_query"]
    assert rule.max_calls == 4
    assert rule.max_arg_bytes == 1024
    assert rule.args["additionalProperties"] is False
    assert rule.args["required"] == ["metric", "group_by", "ticker", "period"]


def test_locked_level_matches_yaml_filters() -> None:
    loaded = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    for metric in loaded["metrics"]:
        name = MetricName(str(metric["name"]))
        if name is MetricName.DEPOSITS_QOQ_GROWTH:
            assert "filter" not in metric
            assert locked_level(name) == locked_level(MetricName.TOTAL_DEPOSITS)
            continue
        match = SIMPLE_FILTER.fullmatch(str(metric.get("filter", "")).strip())
        assert match is not None, name.value
        assert locked_level(name) == EntityLevel(match.group(1))


def test_cite_kind_follows_the_metric_stem() -> None:
    fdic = {
        MetricName.TOTAL_DEPOSITS,
        MetricName.TOTAL_ASSETS,
        MetricName.NET_INCOME,
        MetricName.NIM,
        MetricName.EFFICIENCY_RATIO,
        MetricName.DEPOSITS_QOQ_GROWTH,
    }
    for metric in fdic:
        assert cite_kind(metric) is CiteKind.FDIC
    assert cite_kind(MetricName.HOLDING_COMPANY_ASSETS) is CiteKind.SEC_ASSETS
    assert cite_kind(MetricName.HOLDING_COMPANY_DEPOSITS) is CiteKind.SEC_DEPOSITS
    assert cite_kind(MetricName.HOLDING_COMPANY_NET_INCOME) is CiteKind.SEC_NET_INCOME


def test_parse_uppercases_ticker_and_locks_entity_level() -> None:
    query = parse_metric_query(_wire())
    assert query.ticker == "JPM"
    assert query.entity_level is EntityLevel.BANK_SUBSIDIARY
    assert query.period == date(2024, 12, 31)
    assert query.quarter_start() == date(2024, 10, 1)
    holding = parse_metric_query(
        _wire(metric="holding_company_assets", ticker="bac", period="2024-06-30")
    )
    assert holding.entity_level is EntityLevel.HOLDING_COMPANY
    assert holding.quarter_start() == date(2024, 4, 1)


def test_parse_accepts_the_locked_entity_level() -> None:
    query = parse_metric_query(_wire(entity_level="bank_subsidiary"))
    assert query.entity_level is EntityLevel.BANK_SUBSIDIARY


def test_parse_rejects_a_different_entity_level() -> None:
    try:
        parse_metric_query(_wire(entity_level="holding_company"))
    except ValueError as exc:
        assert "entity_level" in str(exc)
    else:
        raise AssertionError("different entity_level was accepted")


def test_parse_rejects_a_non_quarter_end() -> None:
    for period in ("2024-12-30", "2024-01-31", "2024-02-29", "not-a-date", "2024-12-31T00:00:00"):
        try:
            parse_metric_query(_wire(period=period))
        except ValueError as exc:
            assert "quarter-end" in str(exc)
        else:
            raise AssertionError(f"accepted period {period}")


def test_parse_rejects_unknown_fields_and_metrics() -> None:
    try:
        parse_metric_query(_wire(sql="select 1"))
    except ValueError as exc:
        assert "select" not in str(exc)
    else:
        raise AssertionError("sql key was accepted")
    try:
        parse_metric_query(_wire(metric="cet1"))
    except ValueError as exc:
        assert "cet1" not in str(exc).lower() or "unknown metric" in str(exc)
    else:
        raise AssertionError("unknown metric was accepted")


def test_quarter_start_matches_the_semantic_month_map() -> None:
    expected = {
        "2024-03-31": date(2024, 1, 1),
        "2024-06-30": date(2024, 4, 1),
        "2024-09-30": date(2024, 7, 1),
        "2024-12-31": date(2024, 10, 1),
    }
    for period, start in expected.items():
        assert parse_metric_query(_wire(period=period)).quarter_start() == start


def test_render_figure_strips_trailing_zeros_without_a_scale_word() -> None:
    assert render_figure(Decimal("1234.5000000000")) == "1234.5"
    assert render_figure(Decimal("1234")) == "1234"
    assert render_figure(Decimal("0")) == "0"
    assert render_figure(Decimal("-2.50")) == "-2.5"
    rendered = render_figure(Decimal("10000000000.1000000000"))
    assert rendered == "10000000000.1"
    assert "e" not in rendered.lower()
    assert "," not in rendered


def test_cells_equal_uses_relative_tolerance_and_treats_null_as_a_cell() -> None:
    query = parse_metric_query(_wire(ticker="BAC"))
    other = parse_metric_query(_wire(ticker="WFC"))
    close = MetricCell(query=query, value=Decimal("1.0000000000001"))
    base = MetricCell(query=query, value=Decimal("1"))
    far = MetricCell(query=query, value=Decimal("1.0000000001"))
    empty = MetricCell(query=query, value=None)
    assert cells_equal(base, close)
    assert not cells_equal(base, far)
    assert cells_equal(empty, MetricCell(query=query, value=None))
    assert not cells_equal(empty, base)
    assert not cells_equal(base, MetricCell(query=other, value=Decimal("1")))
