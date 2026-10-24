from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from capitalcite.api.ask_models import AskResponse
from capitalcite.ask.types import Citation
from capitalcite.cli import app
from capitalcite.eval.baseline import Baseline, Thresholds, TrackedRate, compare
from capitalcite.eval.metrics_eval import (
    DEFAULT_V1,
    CoverageClass,
    ItemScore,
    MetricsEvalReport,
    V1Coverage,
    V1Row,
    cover_v1,
    figure_token_in,
    load_metrics_report,
    score_answer,
    update_readme_metrics,
)
from capitalcite.eval.metrics_gold import (
    FdicField,
    FdicPointer,
    MetricGoldItem,
    ResolvedNull,
    ResolvedValue,
)
from capitalcite.eval.runner import EvalUnverified
from capitalcite.metric_query.query import (
    EntityLevel,
    GroupBy,
    MetricCell,
    MetricName,
    MetricQuery,
    MetricQueryError,
)

_SOURCE = FdicPointer(cert="628", repdte="20260630", field=FdicField.DEP)
_ITEM = MetricGoldItem(
    id="m-001",
    question="What were JPM's total deposits in 2026Q2?",
    source=_SOURCE,
)


def test_from_results_reloads_dumped_computed_fields(tmp_path: Path) -> None:
    path = Path("evals/results/3231cd4138ba-metrics.json")
    report = load_metrics_report(path)
    assert report.n == 18
    assert report.agent.exact == TrackedRate(hits=15, items=15)
    assert report.rag.exact == TrackedRate(hits=0, items=15)
    assert report.coverage.hits == 0
    assert report.coverage.items == 56
    result = CliRunner().invoke(
        app,
        ["eval", "--suite", "metrics", "--from-results", str(path)],
    )
    assert result.exit_code == 0, result.output
    assert "agent_exact=15/15" in result.output
    readme = tmp_path / "README.md"
    readme.write_text(
        "pre\n<!-- BEGIN METRICS EVAL -->\n\nold\n<!-- END METRICS EVAL -->\npost\n",
        encoding="utf-8",
    )
    update_readme_metrics(readme, report)
    text = readme.read_text(encoding="utf-8")
    assert "3231cd4138ba-metrics.json" in text
    assert "| agent | fake | 18 | 1.000 | 1.000 |" in text


def test_metrics_suite_rejects_retrieval_flags() -> None:
    result = CliRunner().invoke(app, ["eval", "--suite", "metrics", "--small"])
    assert result.exit_code == 1
    assert "--small" in result.output
    help_text = CliRunner().invoke(app, ["eval", "--help"])
    assert help_text.exit_code == 0
    assert "metrics" in help_text.stdout


def test_compare_fails_when_a_metrics_hit_is_dropped() -> None:
    exact = TrackedRate(hits=15, items=15)
    committed = Baseline(
        quote_sanity=TrackedRate(hits=36, items=77),
        fixture=TrackedRate(hits=3, items=3),
        metrics_agent_exact=exact,
        metrics_agent_refusal=TrackedRate(hits=3, items=3),
        metrics_rag_exact=TrackedRate(hits=0, items=15),
        metrics_rag_refusal=TrackedRate(hits=1, items=3),
        metrics_v1_mart=TrackedRate(hits=0, items=56),
        small_n_items=77,
        small_arms=6,
        small_embedder_id="fake:deterministic",
        thresholds=Thresholds(metrics_max_drop_items=0),
    )
    dropped = committed.model_copy(update={"metrics_agent_exact": TrackedRate(hits=14, items=15)})
    report = compare(dropped, committed)
    assert not report.ok
    assert report.failures[0].metric == "metrics_agent_exact"
    assert "dropped 1" in report.failures[0].detail
    extra = committed.model_copy(update={"metrics_agent_exact": TrackedRate(hits=16, items=15)})
    assert compare(extra, committed).ok


def test_figure_token_respects_minus_zero_scale_and_percent() -> None:
    quote = "total_deposits for JPM at 2026-06-30 is 10. Unit is thousands of USD."
    assert figure_token_in("10", quote)
    assert not figure_token_in("0", quote)
    assert not figure_token_in("0", "10")
    assert not figure_token_in("1", "10")
    assert figure_token_in("0", "The figure is 0%.")
    assert figure_token_in("-12.5", "net income is -12.5.")
    assert not figure_token_in("12.5", "net income is -12.5.")
    assert not figure_token_in("2.82", "2.82 billion")
    assert not figure_token_in("1000", "1,000")
    assert not figure_token_in("10", "10.0")
    assert not figure_token_in("0", "10.0")


def test_score_answer_splits_exact_and_refusal() -> None:
    value = ResolvedValue(value=Decimal("10"), query=_query())
    null = ResolvedNull(query=_query())
    hit = score_answer(value, _answered("deposits were 10."), "agent", _ITEM)
    assert hit.exact is True
    assert hit.refusal is False
    assert hit.cell == "value"
    assert hit.rendered == "10"
    scaled_value = ResolvedValue(value=Decimal("2.82"), query=_query())
    scaled = score_answer(scaled_value, _answered("assets were 2.82 billion."), "agent", _ITEM)
    assert scaled.exact is False
    assert scaled.refusal is False
    assert score_answer(scaled_value, _answered("assets were 2.82."), "agent", _ITEM).exact
    refused_value = score_answer(value, _refused(), "rag", _ITEM)
    assert refused_value.exact is False
    assert refused_value.refusal is False
    assert refused_value.outcome == "refused"
    null_hit = score_answer(null, _refused(), "agent", _ITEM)
    assert null_hit.exact is False
    assert null_hit.refusal is True
    assert null_hit.rendered is None
    null_miss = score_answer(null, _answered("the figure is 10."), "rag", _ITEM)
    assert null_miss.exact is False
    assert null_miss.refusal is False


def test_value_cell_cannot_also_be_a_refusal() -> None:
    with pytest.raises(ValidationError):
        ItemScore(
            item_id="m-001",
            path="agent",
            cell="value",
            exact=True,
            refusal=True,
            outcome="answered",
            source=_SOURCE,
            rendered="10",
        )


def test_report_rates_partition_each_path() -> None:
    value = score_answer(
        ResolvedValue(value=Decimal("-12.5"), query=_query(MetricName.NET_INCOME)),
        _answered("is -12.5."),
        "agent",
        _ITEM,
    )
    null = score_answer(ResolvedNull(query=_query()), _refused(), "agent", _ITEM).model_copy(
        update={"item_id": "m-010"}
    )
    scores = (
        value,
        null,
        value.model_copy(update={"path": "rag"}),
        null.model_copy(update={"path": "rag"}),
    )
    report = MetricsEvalReport(
        git_sha="abc123abc123",
        scores=scores,
        coverage=V1Coverage(rows=(V1Row(item_id="cc-h-001", disposition=CoverageClass.CONCEPT),)),
    )
    assert report.n == 2
    assert report.agent.exact.hits == 1
    assert report.agent.exact.items == 1
    assert report.agent.refusal.hits == 1
    assert report.agent.refusal.items == 1
    assert report.agent.exact.items + report.agent.refusal.items == report.n
    assert report.rag.exact.hits == report.agent.exact.hits


def test_coverage_population_is_the_handwritten_numeric_count() -> None:
    counted = _handwritten_numeric_count(DEFAULT_V1)
    coverage = cover_v1(backend=_Fixed(Decimal("1")))
    assert coverage.items == counted
    assert counted >= 1


def test_coverage_counts_a_non_null_cell_and_ignores_the_answer(tmp_path: Path) -> None:
    question = "What were JPM's total deposits in 2026Q2?"
    first = _v1(tmp_path / "a.jsonl", question, answer="0")
    second = _v1(tmp_path / "b.jsonl", question, answer="not the cell")
    hit = cover_v1(first, backend=_Fixed(Decimal("5")))
    again = cover_v1(second, backend=_Fixed(Decimal("5")))
    assert hit == again
    assert hit.hits == 1
    assert hit.items == 1
    missed = cover_v1(first, backend=_Fixed(None))
    assert missed.hits == 0
    assert missed.rows[0].disposition is CoverageClass.NULL_CELL


def test_coverage_skips_short_aliases_scale_grain_and_two_metrics(tmp_path: Path) -> None:
    path = tmp_path / "v1.jsonl"
    _write(
        path,
        [
            _numeric("n-1", "What was JPM's NIM in 2026Q2?"),
            _numeric("n-2", "What was JPMorgan's 2023 net income?"),
            _numeric("n-3", "What were JPM's total deposits on average in 2026Q2?"),
            _numeric("n-4", "What were JPM's total assets and net income in 2026Q2?"),
            _numeric("n-5", "What were JPM and BAC total deposits in 2026Q2?"),
            _numeric("n-6", "What was the total deposits level in 2026Q2?"),
        ],
    )
    coverage = cover_v1(path, backend=_Fixed(Decimal("1")))
    assert coverage.items == 6
    assert coverage.hits == 0


def test_coverage_warehouse_error_is_a_miss_and_a_missing_backend_is_unverified(
    tmp_path: Path,
) -> None:
    path = _v1(tmp_path / "v1.jsonl", "What were JPM's total deposits in 2026Q2?")
    missed = cover_v1(path, backend=_Broken())
    assert missed.hits == 0
    assert missed.rows[0].disposition is CoverageClass.WAREHOUSE_ERROR
    with pytest.raises(EvalUnverified):
        cover_v1(path, backend=None)
    with pytest.raises(EvalUnverified):
        cover_v1(path, backend=_Missing())


def _query(metric: MetricName = MetricName.TOTAL_DEPOSITS) -> MetricQuery:
    return MetricQuery(
        metric=metric,
        group_by=GroupBy.QUARTER_AND_TICKER,
        ticker="JPM",
        period=date(2026, 6, 30),
        entity_level=EntityLevel.BANK_SUBSIDIARY,
    )


def _answered(text: str) -> AskResponse:
    return AskResponse(
        kind="answered",
        answer=text,
        citations=[
            Citation(
                doc_id="doc",
                start=0,
                end=1,
                quote="q",
                quote_start=0,
                quote_end=1,
                section="Item 7",
                source_url="https://example.com/filing",
            )
        ],
        retrieval_score=1.0,
        config_hash="abc",
        collection_name="filings",
        point_count=1,
    )


def _refused() -> AskResponse:
    return AskResponse(
        kind="refused",
        answer=None,
        citations=[],
        retrieval_score=0.0,
        refusal_reason="metric cell is null",
        config_hash="abc",
        collection_name="filings",
        point_count=1,
    )


class _Fixed:
    def __init__(self, value: Decimal | None) -> None:
        self._value = value

    def execute(self, query: MetricQuery) -> MetricCell:
        return MetricCell(query=query, value=self._value)


class _Broken:
    def execute(self, query: MetricQuery) -> MetricCell:
        raise MetricQueryError("metric query failed")


class _Missing:
    def execute(self, query: MetricQuery) -> MetricCell:
        raise MetricQueryError("warehouse is missing")


def _handwritten_numeric_count(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["type"] == "numeric" and row["source"] == "handwritten":
            count += 1
    return count


def _v1(path: Path, question: str, *, answer: str = "span") -> Path:
    _write(path, [_numeric("n-1", question, answer=answer)])
    return path


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _numeric(item_id: str, question: str, *, answer: str = "span") -> dict[str, object]:
    return {
        "id": item_id,
        "question": question,
        "answer": answer,
        "evidence": [
            {
                "doc_id": "doc",
                "section": "Item 7",
                "quote": "span",
                "role": "answer",
            }
        ],
        "type": "numeric",
        "answerable": True,
        "source": "handwritten",
    }
