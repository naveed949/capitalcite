from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import yaml

from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.tokens import count_tokens
from capitalcite.gate.policy import BrokenPolicy, load_policy
from capitalcite.ingest.client import filing_index_url
from capitalcite.metric_query import (
    MetricCell,
    cells_equal,
    parse_metric_query,
    run_metric_query,
)
from capitalcite.metric_query.cite import (
    CitationBundle,
    CitationResolver,
    CiteKind,
    SourceRef,
    cite_kind,
    hits_for,
)
from capitalcite.metric_query.query import (
    EntityLevel,
    MetricName,
    MetricQuery,
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


_FDIC_URL = "https://banks.data.fdic.gov/api/financials?filters=REPDTE:20241231"
_TEN_K = "0000019617-25-000270"
_NINE_MONTH = "0000019617-24-000890"
_CIK = "0000019617"


class _FixedBackend:
    def __init__(self, cell: MetricCell) -> None:
        self._cell = cell

    def execute(self, query: MetricQuery) -> MetricCell:
        assert query == self._cell.query
        return self._cell


def _cell(metric: str, ticker: str, period: str, value: Decimal | None) -> MetricCell:
    return MetricCell(
        query=parse_metric_query(_wire(metric=metric, ticker=ticker, period=period)),
        value=value,
    )


def _warehouse(path: Path) -> None:
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        create table dim_bank (bank_key varchar, cik varchar);
        create table fct_bank_financials_quarterly (
            bank_key varchar,
            ticker varchar,
            quarter_id date,
            entity_level varchar,
            assets_sec_accn varchar,
            deposits_sec_accn varchar,
            net_income_sec_accn varchar,
            net_income_method varchar,
            net_income_nine_month_sec_accn varchar,
            fdic_repdte varchar,
            fdic_source_url varchar
        );
        """
    )
    connection.execute(
        "insert into dim_bank values (?, ?), (?, ?)",
        [
            "JPM|holding_company",
            _CIK,
            "JPM|bank_subsidiary",
            None,
        ],
    )
    connection.execute(
        """
        insert into fct_bank_financials_quarterly values
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "JPM|holding_company",
            "JPM",
            date(2024, 12, 31),
            "holding_company",
            None,
            None,
            _TEN_K,
            "fy_minus_9m",
            _NINE_MONTH,
            None,
            None,
            "JPM|bank_subsidiary",
            "JPM",
            date(2024, 12, 31),
            "bank_subsidiary",
            None,
            None,
            None,
            None,
            None,
            "20241231",
            _FDIC_URL,
        ],
    )
    connection.close()


def test_fdic_quote_ends_the_first_sentence_on_the_figure(tmp_path: Path) -> None:
    warehouse = tmp_path / "facts.duckdb"
    _warehouse(warehouse)
    cell = _cell("total_deposits", "JPM", "2024-12-31", Decimal("1234.50"))
    result = run_metric_query(
        _wire(),
        backend=_FixedBackend(cell),
        citations=CitationResolver(warehouse),
    )
    assert result.ok is True
    assert result.hits and len(result.hits) == 1
    assert result.value == "1234.5"
    assert result.detail == "1234.5"
    assert result.args["ticker"] == "jpm"
    hit = result.hits[0]
    first, rest = hit.chunk.text.split(". ", 1)
    assert first == "total_deposits for JPM at 2024-12-31 is 1234.5"
    assert "thousand" not in first
    assert rest == (f"Unit is thousands of USD. FDIC REPDTE 20241231. Source {_FDIC_URL}")
    assert hit.chunk.text.count("1234.5") == 1
    assert hit.chunk.ticker == "JPM"
    assert hit.chunk.period == "2024-12-31"
    assert hit.chunk.section == "total_deposits"
    assert hit.chunk.doc_id == "fdic:20241231"
    assert hit.chunk.source_url == _FDIC_URL
    assert hit.chunk.accession == ""
    assert hit.chunk.start == 0
    assert hit.chunk.end == len(hit.chunk.text)
    assert hit.chunk.offset == 0
    assert hit.chunk.header_prefix == ""
    assert hit.chunk.chunker is ChunkerName.FIXED
    assert hit.chunk.token_count == count_tokens(hit.chunk.text)
    assert hit.chunk.token_count >= 1
    assert hit.score == 1.0


def test_percent_quote_repeats_the_figure_with_a_percent_sign() -> None:
    cell = _cell("nim", "BAC", "2024-09-30", Decimal("2.450"))
    source = SourceRef(
        kind="fdic",
        doc_id="fdic:20240930",
        source_url="https://example.test/nim",
        label="REPDTE 20240930",
    )
    hit = hits_for(cell, CitationBundle(sources=(source,)))[0]
    first, rest = hit.chunk.text.split(". ", 1)
    assert first == "nim for BAC at 2024-09-30 is 2.45"
    assert rest.startswith("Unit is percent. The figure is 2.45%.")
    assert "FDIC REPDTE 20240930. Source https://example.test/nim" in hit.chunk.text
    assert hit.chunk.section == "nim"


def test_fy_minus_9m_returns_two_hits_with_the_same_figure(tmp_path: Path) -> None:
    warehouse = tmp_path / "facts.duckdb"
    _warehouse(warehouse)
    cell = _cell("holding_company_net_income", "JPM", "2024-12-31", Decimal("49000000000"))
    result = run_metric_query(
        _wire(metric="holding_company_net_income"),
        backend=_FixedBackend(cell),
        citations=CitationResolver(warehouse),
    )
    assert result.ok is True
    assert len(result.hits) == 2
    figure = "49000000000"
    assert result.value == figure
    accessions = []
    for hit in result.hits:
        assert figure in hit.chunk.text
        assert hit.chunk.period == "2024-12-31"
        assert hit.chunk.section == "holding_company_net_income"
        assert hit.chunk.source_url == filing_index_url(_CIK, hit.chunk.accession)
        first = hit.chunk.text.split(". ", 1)[0]
        assert first.endswith(f"is {figure}")
        assert "Unit is whole USD." in hit.chunk.text
        accessions.append(hit.chunk.accession)
    assert accessions == [_TEN_K, _NINE_MONTH]


def test_growth_cites_only_the_current_fdic_row(tmp_path: Path) -> None:
    warehouse = tmp_path / "facts.duckdb"
    _warehouse(warehouse)
    cell = _cell("deposits_qoq_growth", "JPM", "2024-12-31", Decimal("0.05"))
    result = run_metric_query(
        _wire(metric="deposits_qoq_growth"),
        backend=_FixedBackend(cell),
        citations=CitationResolver(warehouse),
    )
    assert result.ok is True
    assert len(result.hits) == 1
    assert result.hits[0].chunk.doc_id == "fdic:20241231"
    assert "Unit is a ratio." in result.hits[0].chunk.text
    assert result.hits[0].chunk.text.count("0.05") == 1


def test_null_cell_returns_no_hit_and_no_figure() -> None:
    cell = _cell("total_deposits", "JPM", "2024-12-31", None)

    class _NullBackend:
        def execute(self, query: MetricQuery) -> MetricCell:
            return cell

    result = run_metric_query(
        _wire(),
        backend=_NullBackend(),
        citations=CitationResolver(Path("/no/such/warehouse.duckdb")),
    )
    assert result.ok is False
    assert result.hits == ()
    assert result.detail == "metric cell is null"
    assert not any(character.isdigit() for character in result.detail)


def test_missing_accession_hides_the_figure(tmp_path: Path) -> None:
    warehouse = tmp_path / "facts.duckdb"
    _warehouse(warehouse)
    connection = duckdb.connect(str(warehouse))
    connection.execute(
        """
        insert into dim_bank values (?, ?)
        """,
        ["BAC|holding_company", _CIK],
    )
    connection.execute(
        """
        insert into fct_bank_financials_quarterly values
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "BAC|holding_company",
            "BAC",
            date(2024, 12, 31),
            "holding_company",
            None,
            None,
            _TEN_K,
            "fy_minus_9m",
            None,
            None,
            None,
        ],
    )
    connection.close()
    cell = _cell("holding_company_net_income", "BAC", "2024-12-31", Decimal("99.5"))
    result = run_metric_query(
        _wire(metric="holding_company_net_income", ticker="BAC"),
        backend=_FixedBackend(cell),
        citations=CitationResolver(warehouse),
    )
    assert result.ok is False
    assert result.hits == ()
    assert "99.5" not in result.detail
