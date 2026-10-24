from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from capitalcite.agent.models import FakeAgentModel
from capitalcite.eval.metrics_gold import (
    DEFAULT_FIXTURE_ROOT,
    DEFAULT_METRICS_ITEMS,
    FdicField,
    FdicGrowthPointer,
    FdicPointer,
    MetricGoldItem,
    ResolvedNull,
    ResolvedValue,
    SecPointer,
    implied_query,
    load_metric_gold,
    resolve,
)
from capitalcite.metric_query.query import parse_metric_query

_FORBIDDEN = frozenset({"answer", "expected", "value", "figure", "expected_figure"})
_ROOT = DEFAULT_FIXTURE_ROOT


def test_gold_file_stores_no_figure() -> None:
    text = DEFAULT_METRICS_ITEMS.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(rows) >= 15
    for row in rows:
        assert set(row) == {"id", "question", "source"}
        assert _FORBIDDEN.isdisjoint(_keys(row))


def test_resolve_matches_an_independent_fixture_walk() -> None:
    items = _items()
    negatives = 0
    nulls = 0
    zeros = 0
    tickers: set[str] = set()
    for item in items:
        got = resolve(item.source, _ROOT)
        walked = _walk(item.source, _ROOT)
        tickers.add(walked.ticker)
        if walked.value is None:
            assert isinstance(got, ResolvedNull)
            nulls += 1
            continue
        assert isinstance(got, ResolvedValue)
        assert got.value == walked.value
        if walked.value < 0:
            negatives += 1
        if walked.value == 0:
            zeros += 1
    assert tickers == {"JPM", "BAC", "WFC", "C"}
    assert negatives >= 1
    assert nulls >= 1
    assert zeros >= 1


async def test_load_accepts_the_suite() -> None:
    items = await load_metric_gold()
    assert len(items) >= 15


async def test_fake_planner_emits_the_implied_query() -> None:
    items = _items()
    model = FakeAgentModel()
    for item in items:
        decision = await model.plan(item.question, [], [])
        assert decision.kind == "plan"
        assert len(decision.calls) == 1
        call = decision.calls[0]
        assert call.name == "metric_query"
        assert parse_metric_query(call.args) == implied_query(item.source, _ROOT)


def test_item_model_rejects_an_answer_field() -> None:
    raw = {
        "id": "m-001",
        "question": "What were JPM's total deposits in 2026Q2?",
        "answer": "1",
        "source": {
            "kind": "fdic",
            "cert": "628",
            "repdte": "20260630",
            "field": "DEP",
        },
    }
    with pytest.raises(ValidationError):
        MetricGoldItem.model_validate(raw)


class _Walked:
    def __init__(self, ticker: str, value: Decimal | None) -> None:
        self.ticker = ticker
        self.value = value


def _walk(source: FdicPointer | FdicGrowthPointer | SecPointer, root: Path) -> _Walked:
    banks = _banks(root)
    if isinstance(source, SecPointer):
        return _walk_sec(source, banks, root)
    ticker = banks[source.cert]
    rows = _fdic(root, source.cert)
    if isinstance(source, FdicGrowthPointer):
        return _Walked(ticker, _growth(rows, source.repdte))
    return _Walked(ticker, _fdic_value(rows, source))


def _walk_sec(source: SecPointer, banks: dict[str, str], root: Path) -> _Walked:
    assert source.ticker in banks.values()
    concepts = _sec(root, source.ticker)
    end = source.end.isoformat()
    if source.concept == "Assets":
        hits = _points(concepts.get("Assets", ()), source.accn, end)
        assert len(hits) == 1
        return _Walked(source.ticker, hits[0])
    named = _points(concepts.get(source.concept, ()), source.accn, end)
    assert not named
    assert any(_points(points, source.accn, end) for points in concepts.values())
    return _Walked(source.ticker, None)


def _fdic_value(rows: dict[str, dict[str, Decimal]], source: FdicPointer) -> Decimal | None:
    current = rows[source.repdte][source.field.value]
    if source.field is not FdicField.NETINC:
        return current
    day = _day(source.repdte)
    if day.month == 3:
        return current
    prior_day = _prior_quarter_end(day)
    if prior_day.year != day.year:
        return None
    prior = prior_day.strftime("%Y%m%d")
    if prior not in rows or "NETINC" not in rows[prior]:
        return None
    return current - rows[prior]["NETINC"]


def _growth(rows: dict[str, dict[str, Decimal]], repdte: str) -> Decimal | None:
    day = _day(repdte)
    prior = _prior_quarter_end(day).strftime("%Y%m%d")
    if prior not in rows:
        return None
    prior_dep = rows[prior]["DEP"]
    if prior_dep == 0:
        return None
    return (rows[repdte]["DEP"] - prior_dep) / prior_dep


def _prior_quarter_end(day: date) -> date:
    start_month = {3: 1, 6: 4, 9: 7, 12: 10}[day.month]
    return date(day.year, start_month, 1) - timedelta(days=1)


def _items() -> tuple[MetricGoldItem, ...]:
    lines = DEFAULT_METRICS_ITEMS.read_text(encoding="utf-8").splitlines()
    return tuple(MetricGoldItem.model_validate_json(line) for line in lines if line.strip())


def _banks(root: Path) -> dict[str, str]:
    payload = _load(root / "lead_banks.json")
    assert isinstance(payload, list)
    found: dict[str, str] = {}
    for row in payload:
        assert isinstance(row, dict)
        cert = row["cert"]
        ticker = row["ticker"]
        assert isinstance(cert, str)
        assert isinstance(ticker, str)
        found[cert] = ticker
    return found


def _fdic(root: Path, cert: str) -> dict[str, dict[str, Decimal]]:
    payload = _load(root / "fdic" / f"{cert}.json")
    assert isinstance(payload, dict)
    raw_rows = payload["data"]
    assert isinstance(raw_rows, list)
    found: dict[str, dict[str, Decimal]] = {}
    for raw in raw_rows:
        assert isinstance(raw, dict)
        data = raw["data"]
        assert isinstance(data, dict)
        repdte = data["REPDTE"]
        assert isinstance(repdte, str)
        fields: dict[str, Decimal] = {}
        for key, value in data.items():
            if isinstance(value, Decimal):
                fields[key] = value
        found[repdte] = fields
    return found


def _sec(root: Path, ticker: str) -> dict[str, tuple[tuple[str, str, Decimal], ...]]:
    payload = _load(root / "xbrl" / f"{ticker}.json")
    assert isinstance(payload, dict)
    facts = payload["facts"]
    assert isinstance(facts, dict)
    gaap = facts["us-gaap"]
    assert isinstance(gaap, dict)
    concepts: dict[str, tuple[tuple[str, str, Decimal], ...]] = {}
    for name, raw in gaap.items():
        assert isinstance(name, str)
        assert isinstance(raw, dict)
        units = raw["units"]
        assert isinstance(units, dict)
        points = units["USD"]
        assert isinstance(points, list)
        parsed: list[tuple[str, str, Decimal]] = []
        for point in points:
            assert isinstance(point, dict)
            accn = point["accn"]
            end = point["end"]
            val = point["val"]
            assert isinstance(accn, str)
            assert isinstance(end, str)
            assert isinstance(val, Decimal)
            parsed.append((accn, end, val))
        concepts[name] = tuple(parsed)
    return concepts


def _points(
    points: tuple[tuple[str, str, Decimal], ...],
    accn: str,
    end: str,
) -> tuple[Decimal, ...]:
    return tuple(
        val for point_accn, point_end, val in points if point_accn == accn and point_end == end
    )


def _day(repdte: str) -> date:
    return date(int(repdte[0:4]), int(repdte[4:6]), int(repdte[6:8]))


def _load(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle, parse_float=Decimal, parse_int=Decimal)


def _keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                found.add(key)
            found.update(_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_keys(item))
    return found
