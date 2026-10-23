from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
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
from capitalcite.metric_query.backends import (
    SENTINEL_QUARTER_END,
    SENTINEL_TICKER,
    Catalog,
    CatalogEntry,
    CatalogSql,
    LiveMetricFlow,
    compile_catalog,
    load_catalog,
    mf_argv,
    parameterize,
    sql_from_explain,
    where_clause,
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
    GroupBy,
    MetricName,
    MetricQuery,
    MetricQueryError,
    locked_level,
    render_figure,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "dbt" / "models" / "semantic" / "bank_financials.yml"
TOOLS = ROOT / "src" / "capitalcite" / "gate" / "tools.json"
QUERY_CATALOG = ROOT / "src" / "capitalcite" / "metric_query" / "catalog.json"
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
    catalog = json.loads(QUERY_CATALOG.read_text(encoding="utf-8"))
    catalog_names = {str(entry["metric"]) for entry in catalog["entries"]}
    assert yaml_names == enum_names == gate_names == catalog_names
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


def test_parameterize_replaces_quoted_sentinels_with_bare_placeholders() -> None:
    sql = (
        "ticker = 'ZZQZZQ' and start = '1999-10-01' and again = 'ZZQZZQ' "
        "and bare = ZZQZZQ and note = 'keep'"
    )
    rewritten, order = parameterize(sql)
    assert rewritten == (
        "ticker = ? and start = ? and again = ? and bare = ZZQZZQ and note = 'keep'"
    )
    assert order == ("ticker", "quarter_start", "ticker")
    assert "'?'" not in rewritten


def test_sql_from_explain_strips_ansi_and_keeps_the_statement() -> None:
    stdout = (
        "\x1b[31mplan\x1b[0m\n"
        "Query plan\n"
        "SELECT a FROM t WHERE ticker = 'ZZQZZQ' AND start = '1999-10-01'\n"
    )
    sql = sql_from_explain(stdout)
    assert sql.startswith("SELECT a FROM t")
    assert "plan" not in sql.splitlines()[0]
    with pytest.raises(MetricQueryError):
        sql_from_explain("SELECT 1")


def test_where_clause_emits_typed_fields_in_order() -> None:
    query = MetricQuery(
        metric=MetricName.TOTAL_DEPOSITS,
        group_by=GroupBy.QUARTER_AND_TICKER,
        ticker=SENTINEL_TICKER,
        period=SENTINEL_QUARTER_END,
        entity_level=locked_level(MetricName.TOTAL_DEPOSITS),
    )
    text = where_clause(query)
    assert text == (
        "{{ Dimension('bank__ticker') }} = 'ZZQZZQ' AND "
        "{{ TimeDimension('metric_time', 'quarter') }} = '1999-10-01' AND "
        "{{ Dimension('bank__entity_level') }} = 'bank_subsidiary'"
    )
    argv = mf_argv(query, mode="explain")
    assert argv[:6] == ["uv", "run", "--group", "metrics", "mf", "query"]
    assert "--target" not in argv
    assert argv[-1] == "--explain"
    assert "10" in argv


def test_compile_catalog_parameterizes_and_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explain = (
        "SELECT ticker, quarter FROM bank WHERE ticker = 'ZZQZZQ' AND quarter = '1999-10-01'\n"
    )
    seen_env: list[Mapping[str, str]] = []

    def fake_run(
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == ROOT
        assert check is False
        assert capture_output is True
        assert text is True
        assert env is not os.environ
        assert env["DBT_TARGET"] == "ci"
        seen_env.append(env)
        assert "--explain" in argv
        return subprocess.CompletedProcess(argv, 0, stdout=explain, stderr="")

    monkeypatch.setattr("capitalcite.metric_query.backends.subprocess.run", fake_run)
    target_before = os.environ.get("DBT_TARGET")
    first = tmp_path / "catalog.json"
    second = tmp_path / "again.json"
    compile_catalog(first, cwd=ROOT, env={"DBT_TARGET": "ci"})
    compile_catalog(second, cwd=ROOT, env={"DBT_TARGET": "ci"})
    assert first.read_bytes() == second.read_bytes()
    assert os.environ.get("DBT_TARGET") == target_before
    catalog = load_catalog(first)
    assert catalog.version == 1
    assert catalog.duckdb == "2.0.0"
    assert catalog.semantic_sha256 == hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    assert len(catalog.entries) == len(MetricName)
    assert len(seen_env) == len(MetricName) * 2
    entry = catalog.by_key()[(MetricName.NIM, GroupBy.QUARTER_AND_TICKER)]
    assert entry.entity_level is EntityLevel.BANK_SUBSIDIARY
    assert entry.param_order == ("ticker", "quarter_start")
    assert entry.sql == ("SELECT ticker, quarter FROM bank WHERE ticker = ? AND quarter = ?")
    assert "ZZQZZQ" not in entry.sql
    assert "'?'" not in entry.sql


@pytest.mark.parametrize(
    "stdout",
    [
        "SELECT 1\n",
        "SELECT 'ZZQZZQ' AS ticker, '1999-10-01' AS start, '1999-12-31' AS grain\n",
        "SELECT 'ZZQZZQ' AS ticker, ZZQZZQ AS bare, '1999-10-01' AS start\n",
        "SELECT 'ZZQZZQ' AS ticker; SELECT '1999-10-01' AS start\n",
    ],
)
def test_compile_rejects_a_changed_sentinel_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    def fake_run(
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        _ = argv, cwd, env, check, capture_output, text
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="select secret")

    monkeypatch.setattr("capitalcite.metric_query.backends.subprocess.run", fake_run)
    with pytest.raises(MetricQueryError) as caught:
        compile_catalog(tmp_path / "bad.json", cwd=ROOT, env={"DBT_TARGET": "ci"})
    assert "select secret" not in str(caught.value)
    assert "1999-12-31" not in str(caught.value)


def test_load_catalog_rejects_extra_keys_and_a_bad_version(tmp_path: Path) -> None:
    good = load_catalog(QUERY_CATALOG)
    assert good.semantic_sha256 == hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    payload = json.loads(QUERY_CATALOG.read_text(encoding="utf-8"))
    payload["extra"] = "nope"
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MetricQueryError) as caught:
        load_catalog(extra)
    assert "nope" not in str(caught.value)
    payload.pop("extra")
    payload["version"] = 2
    bad_version = tmp_path / "version.json"
    bad_version.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MetricQueryError):
        load_catalog(bad_version)


def test_catalog_sql_returns_one_decimal_cell(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(warehouse))
    connection.execute(
        """
        create table figures (
            ticker varchar,
            quarter_start varchar,
            total_deposits varchar
        )
        """
    )
    connection.execute(
        "insert into figures values (?, ?, ?)",
        ["JPM", "2024-10-01", "1234.50"],
    )
    connection.close()
    entry = CatalogEntry(
        metric=MetricName.TOTAL_DEPOSITS,
        group_by=GroupBy.QUARTER_AND_TICKER,
        entity_level=EntityLevel.BANK_SUBSIDIARY,
        sql=(
            "select ticker as bank__ticker, quarter_start as metric_time__quarter, "
            "total_deposits from figures where ticker = ? and quarter_start = ?"
        ),
        param_order=("ticker", "quarter_start"),
    )
    catalog = Catalog(
        version=1,
        semantic_sha256="a" * 64,
        duckdb="2.0.0",
        entries=(entry,),
    )
    cell = CatalogSql(catalog, warehouse).execute(parse_metric_query(_wire()))
    assert cell.value == Decimal("1234.50")
    assert cell.query.ticker == "JPM"


def test_catalog_sql_treats_null_and_blank_as_a_null_cell(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(warehouse))
    connection.execute("create table figures (ticker varchar, quarter_start varchar, nim varchar)")
    connection.execute("insert into figures values ('BAC', '2024-10-01', null)")
    connection.close()
    entry = CatalogEntry(
        metric=MetricName.NIM,
        group_by=GroupBy.QUARTER_AND_TICKER,
        entity_level=EntityLevel.BANK_SUBSIDIARY,
        sql=(
            "select ticker as bank__ticker, quarter_start as metric_time__quarter, "
            "nim from figures where ticker = ? and quarter_start = ?"
        ),
        param_order=("ticker", "quarter_start"),
    )
    catalog = Catalog(
        version=1,
        semantic_sha256="b" * 64,
        duckdb="2.0.0",
        entries=(entry,),
    )
    query = parse_metric_query(_wire(metric="nim", ticker="BAC"))
    assert CatalogSql(catalog, warehouse).execute(query).value is None

    connection = duckdb.connect(str(warehouse))
    connection.execute("update figures set nim = ''")
    connection.close()
    assert CatalogSql(catalog, warehouse).execute(query).value is None


def test_catalog_sql_hides_statement_text_on_failure(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    duckdb.connect(str(warehouse)).close()
    marker = "not_a_real_relation_zzq"
    entry = CatalogEntry(
        metric=MetricName.TOTAL_ASSETS,
        group_by=GroupBy.QUARTER_AND_TICKER,
        entity_level=EntityLevel.BANK_SUBSIDIARY,
        sql=f"select {marker} from missing_table where ticker = ? and quarter_start = ?",
        param_order=("ticker", "quarter_start"),
    )
    catalog = Catalog(
        version=1,
        semantic_sha256="c" * 64,
        duckdb="2.0.0",
        entries=(entry,),
    )
    with pytest.raises(MetricQueryError) as caught:
        CatalogSql(catalog, warehouse).execute(_wire_query("total_assets"))
    assert marker not in str(caught.value)
    assert "missing_table" not in str(caught.value)


def test_catalog_sql_rejects_a_level_mismatch_and_a_duplicate(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    duckdb.connect(str(warehouse)).close()
    entry = CatalogEntry(
        metric=MetricName.TOTAL_DEPOSITS,
        group_by=GroupBy.QUARTER_AND_TICKER,
        entity_level=EntityLevel.HOLDING_COMPANY,
        sql="select 1 as total_deposits where ? = ? ",
        param_order=("ticker", "quarter_start"),
    )
    catalog = Catalog(
        version=1,
        semantic_sha256="d" * 64,
        duckdb="2.0.0",
        entries=(entry,),
    )
    with pytest.raises(MetricQueryError) as caught:
        CatalogSql(catalog, warehouse).execute(parse_metric_query(_wire()))
    assert "holding_company" not in str(caught.value)
    duplicate = Catalog(
        version=1,
        semantic_sha256="d" * 64,
        duckdb="2.0.0",
        entries=(entry, entry),
    )
    with pytest.raises(MetricQueryError):
        duplicate.by_key()


def _wire_query(metric: str) -> MetricQuery:
    return parse_metric_query(_wire(metric=metric))


def test_growth_where_keeps_the_quarter_literal_without_filtering_inputs() -> None:
    query = MetricQuery(
        metric=MetricName.DEPOSITS_QOQ_GROWTH,
        group_by=GroupBy.QUARTER_AND_TICKER,
        ticker=SENTINEL_TICKER,
        period=SENTINEL_QUARTER_END,
        entity_level=locked_level(MetricName.DEPOSITS_QOQ_GROWTH),
    )
    text = where_clause(query)
    assert text.index("ZZQZZQ") < text.index("1999-10-01") < text.index("bank_subsidiary")
    assert "({{ TimeDimension('metric_time', 'quarter') }} = '1999-10-01' OR 1 = 1)" in text


def test_catalog_sql_keeps_the_requested_quarter(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(warehouse))
    connection.execute(
        """
        create table figures (
            ticker varchar,
            quarter_start varchar,
            deposits_qoq_growth varchar
        )
        """
    )
    connection.execute(
        "insert into figures values ('JPM', '2024-07-01', '0.10'), ('JPM', '2024-10-01', '0.25')"
    )
    connection.close()
    entry = CatalogEntry(
        metric=MetricName.DEPOSITS_QOQ_GROWTH,
        group_by=GroupBy.QUARTER_AND_TICKER,
        entity_level=EntityLevel.BANK_SUBSIDIARY,
        sql=(
            "select ticker as bank__ticker, quarter_start as metric_time__quarter, "
            "deposits_qoq_growth from figures where ticker = ?"
        ),
        param_order=("ticker",),
    )
    catalog = Catalog(
        version=1,
        semantic_sha256="e" * 64,
        duckdb="2.0.0",
        entries=(entry,),
    )
    cell = CatalogSql(catalog, warehouse).execute(
        parse_metric_query(_wire(metric="deposits_qoq_growth", period="2024-12-31"))
    )
    assert cell.value == Decimal("0.25")


def _mf_env() -> dict[str, str]:
    env = dict(os.environ)
    env["DBT_TARGET"] = "ci"
    env["DBT_PROJECT_DIR"] = str(ROOT / "dbt")
    env["DBT_PROFILES_DIR"] = str(ROOT / "dbt")
    return env


@pytest.mark.mf
def test_committed_catalog_matches_compile_and_stale_bytes_differ(tmp_path: Path) -> None:
    if os.environ.get("DBT_TARGET") != "ci":
        raise AssertionError("DBT_TARGET must be ci before mf query")
    compiled = tmp_path / "catalog.json"
    compile_catalog(compiled, cwd=ROOT, env=_mf_env())
    fresh = compiled.read_bytes()
    assert fresh == QUERY_CATALOG.read_bytes()
    payload = json.loads(fresh)
    payload["semantic_sha256"] = "0" * 64
    mutated_hash = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    assert mutated_hash.encode() != fresh
    payload = json.loads(fresh)
    payload["entries"][0]["sql"] = payload["entries"][0]["sql"] + " "
    mutated_sql = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    assert mutated_sql.encode() != fresh


_NULL_ON_FIXTURES = {
    MetricName.HOLDING_COMPANY_DEPOSITS,
    MetricName.HOLDING_COMPANY_NET_INCOME,
}


@pytest.mark.mf
def test_catalog_matches_live_metricflow_on_golden_tickers() -> None:
    if os.environ.get("DBT_TARGET") != "ci":
        raise AssertionError("DBT_TARGET must be ci before mf query")
    warehouse = ROOT / "dbt" / "target" / "ci.duckdb"
    assert warehouse.is_file()
    catalog = CatalogSql(load_catalog(QUERY_CATALOG), warehouse)
    live = LiveMetricFlow(cwd=ROOT, env=_mf_env())
    cases: list[tuple[str, str, MetricName]] = []
    for ticker in ("JPM", "BAC", "WFC", "C"):
        for metric in MetricName:
            if metric in {
                MetricName.HOLDING_COMPANY_ASSETS,
                MetricName.HOLDING_COMPANY_DEPOSITS,
                MetricName.HOLDING_COMPANY_NET_INCOME,
            }:
                continue
            cases.append((ticker, "2026-06-30", metric))
    for ticker in ("JPM", "BAC", "WFC"):
        for metric in (
            MetricName.HOLDING_COMPANY_ASSETS,
            MetricName.HOLDING_COMPANY_DEPOSITS,
            MetricName.HOLDING_COMPANY_NET_INCOME,
        ):
            cases.append((ticker, "2026-06-30", metric))
    for metric in (
        MetricName.HOLDING_COMPANY_ASSETS,
        MetricName.HOLDING_COMPANY_DEPOSITS,
        MetricName.HOLDING_COMPANY_NET_INCOME,
    ):
        cases.append(("C", "2025-12-31", metric))
    for ticker, period, metric in cases:
        query = parse_metric_query(_wire(metric=metric.value, ticker=ticker, period=period))
        catalog_cell = catalog.execute(query)
        live_cell = live.execute(query)
        assert cells_equal(live_cell, catalog_cell), (
            ticker,
            period,
            metric.value,
            live_cell.value,
            catalog_cell.value,
        )
        if metric in _NULL_ON_FIXTURES:
            assert live_cell.value is None
            assert catalog_cell.value is None
        else:
            assert live_cell.value is not None
            assert catalog_cell.value is not None
