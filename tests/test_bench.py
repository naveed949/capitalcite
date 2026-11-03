from __future__ import annotations

import json
from pathlib import Path

import pytest

from capitalcite.eval.bench import (
    RECALL_NOT_MEASURED,
    BenchPin,
    BenchReport,
    QueryRecord,
    RecallReport,
)
from capitalcite.eval.bench_gate import (
    BenchBaseline,
    compare_bench,
    perturb_p95,
    snapshot_bench,
)
from capitalcite.eval.bootstrap import MeanCI
from capitalcite.eval.fixture_index import indexed_corpus
from capitalcite.eval.phases import fold_phases
from capitalcite.eval.prices import load_prices
from capitalcite.eval.runner import EvalUnverified
from capitalcite.trace.types import ObservationKind, SpanRecord


def _span(
    *,
    name: str,
    kind: ObservationKind,
    span_id: str,
    parent_id: str | None,
    duration_ms: float,
) -> SpanRecord:
    return SpanRecord(
        name=name,
        kind=kind,
        trace_id="t",
        span_id=span_id,
        parent_id=parent_id,
        duration_ms=duration_ms,
    )


def test_fold_phases_excludes_nested_retrieve_from_tool() -> None:
    spans = (
        _span(name="ask", kind=ObservationKind.ASK, span_id="a", parent_id=None, duration_ms=40),
        _span(name="tool", kind=ObservationKind.TOOL, span_id="t", parent_id="a", duration_ms=20),
        _span(
            name="retrieve",
            kind=ObservationKind.RETRIEVE,
            span_id="r",
            parent_id="t",
            duration_ms=12,
        ),
        _span(
            name="generate",
            kind=ObservationKind.GENERATE,
            span_id="g",
            parent_id="a",
            duration_ms=5,
        ),
        _span(
            name="compile",
            kind=ObservationKind.AGENT,
            span_id="c",
            parent_id="a",
            duration_ms=8,
        ),
    )
    begins = {"a": 0.0, "g": 0.01}
    phases = fold_phases(spans, begins)
    assert phases.retrieval_ms == 12
    assert phases.tool_ms == 8
    assert phases.model_ms == 5
    assert phases.compile_ms == 8
    assert phases.total_ms == 40
    assert phases.ttft_ms == 10
    assert phases.tool_ms + phases.retrieval_ms + phases.model_ms + phases.compile_ms <= (
        phases.total_ms
    )


def test_fold_phases_ignores_overlapping_graph_nodes() -> None:
    spans = (
        _span(name="ask", kind=ObservationKind.ASK, span_id="a", parent_id=None, duration_ms=50),
        _span(
            name="agent",
            kind=ObservationKind.AGENT,
            span_id="ag",
            parent_id="a",
            duration_ms=48,
        ),
        _span(
            name="compile", kind=ObservationKind.AGENT, span_id="c", parent_id="ag", duration_ms=3
        ),
        _span(
            name="graph.plan",
            kind=ObservationKind.AGENT,
            span_id="gp",
            parent_id="ag",
            duration_ms=6,
        ),
        _span(
            name="plan", kind=ObservationKind.GENERATE, span_id="g1", parent_id="gp", duration_ms=4
        ),
        _span(
            name="graph.act",
            kind=ObservationKind.AGENT,
            span_id="ga",
            parent_id="ag",
            duration_ms=20,
        ),
        _span(name="tool", kind=ObservationKind.TOOL, span_id="t", parent_id="ga", duration_ms=18),
        _span(
            name="retrieve",
            kind=ObservationKind.RETRIEVE,
            span_id="r",
            parent_id="t",
            duration_ms=10,
        ),
        _span(
            name="graph.synthesize",
            kind=ObservationKind.AGENT,
            span_id="gs",
            parent_id="ag",
            duration_ms=8,
        ),
        _span(
            name="complete",
            kind=ObservationKind.GENERATE,
            span_id="g2",
            parent_id="gs",
            duration_ms=5,
        ),
        _span(
            name="graph.stop",
            kind=ObservationKind.AGENT,
            span_id="gst",
            parent_id="ag",
            duration_ms=0.1,
        ),
    )
    phases = fold_phases(spans)
    assert phases.retrieval_ms == 10
    assert phases.tool_ms == 8
    assert phases.model_ms == 9
    assert phases.compile_ms == 3
    assert phases.total_ms == 50
    assert phases.tool_ms + phases.retrieval_ms + phases.model_ms + phases.compile_ms <= (
        phases.total_ms
    )


def _baseline(**updates: object) -> BenchBaseline:
    payload: dict[str, object] = {
        "n_items": 95,
        "model": "fake",
        "compile_once": True,
        "graph_over_rag_p95": 2.0,
        "graph_p95_total_ms": 40.0,
        "rag_p95_total_ms": 20.0,
        "mean_cost_usd": 0.0,
        "mean_compile_ms": 0.0,
    }
    payload.update(updates)
    return BenchBaseline.model_validate(payload)


def test_gate_passes_at_baseline_and_fails_when_ratio_is_tripled() -> None:
    committed = _baseline()
    assert compare_bench(committed, committed).ok is True
    blown = perturb_p95(committed, 3.0)
    report = compare_bench(blown, committed)
    assert report.ok is False
    assert any(row.metric == "graph_over_rag_p95" for row in report.failures)


def test_gate_fails_on_positive_fake_cost() -> None:
    committed = _baseline()
    current = committed.model_copy(update={"mean_cost_usd": 0.01})
    report = compare_bench(current, committed)
    assert report.ok is False
    assert any(row.metric == "mean_cost_usd" for row in report.failures)


def test_gate_fails_compile_once_when_mean_compile_ms_is_half_or_more() -> None:
    committed = _baseline()
    current = committed.model_copy(update={"mean_compile_ms": 0.5})
    report = compare_bench(current, committed)
    assert report.ok is False
    assert any(row.metric == "mean_compile_ms" for row in report.failures)


def test_gate_skips_compile_ms_when_compile_once_is_false() -> None:
    committed = _baseline(compile_once=False, mean_compile_ms=10.0)
    current = committed.model_copy(update={"mean_compile_ms": 10.0})
    report = compare_bench(current, committed)
    assert report.ok is True
    assert not any(row.metric == "mean_compile_ms" for row in report.failures)


def _ci() -> MeanCI:
    return MeanCI(point=0.0, lo=0.0, hi=0.0, B=10000, seed=20261103, n=1)


def _pin() -> BenchPin:
    return BenchPin(
        n_items=1,
        sets={"v1_recall": 1, "metrics": 0},
        openai_model="gpt-4o-mini",
        seed=20261103,
        temperature=0.0,
        tokenizer="o200k_base",
        prompt_hash="a" * 64,
        prices="evals/bench/prices/openai-2026-11-03.json",
        bootstrap_B=10000,
        bootstrap_seed=20261103,
        paid_run={"status": "UNVERIFIED"},
    )


def _row(arm: str, total: float, accurate: bool) -> QueryRecord:
    from capitalcite.eval.phases import PhaseTimes

    return QueryRecord(
        item_id="x",
        item_set="v1_recall",
        arm=arm,  # type: ignore[arg-type]
        kind="answered",
        answer="yes",
        phases=PhaseTimes(
            ttft_ms=1,
            tool_ms=0,
            retrieval_ms=2,
            model_ms=1,
            compile_ms=0,
            total_ms=total,
        ),
        tokens_in=10,
        tokens_out=1,
        cost_usd=0.0,
        recall_at_5=None,
        accurate=accurate,
    )


def test_snapshot_bench_uses_same_run_graph_over_rag() -> None:
    report = BenchReport(
        git_sha="deadbeefdead",
        compile_once=True,
        pin=_pin(),
        prices=load_prices(),
        n_items=1,
        queries=(_row("rag", 10, True), _row("graph", 30, True), _row("loop", 20, True)),
        identity_digest="b" * 64,
        recall=RecallReport(status="not_measured", reason=RECALL_NOT_MEASURED),
        accuracy_delta_graph_minus_loop=_ci(),
        accuracy_delta_graph_minus_rag=_ci(),
        bottleneck_phase="retrieval",
        estimated_paid_tokens_per_item={"tokens_in": 10.0, "tokens_out": 1.0},
    )
    snap = snapshot_bench(report)
    assert snap.graph_p95_total_ms == 30.0
    assert snap.rag_p95_total_ms == 10.0
    assert snap.graph_over_rag_p95 == 3.0
    assert snap.mean_cost_usd == 0.0
    assert snap.mean_compile_ms == 0.0
    assert snap.n_items == 1
    assert snap.schema_version == 2
    assert report.arms["graph"].recall_at_5 is None
    dumped = json.loads(report.model_dump_json())
    assert dumped["arms"]["graph"]["recall_at_5"] is None
    assert dumped["arms"]["rag"]["recall_at_5"] is None
    assert dumped["arms"]["loop"]["recall_at_5"] is None


def test_paid_requires_openai_model() -> None:
    from capitalcite.eval.bench import _bench_settings

    with pytest.raises(EvalUnverified, match="--paid requires ASK_MODEL=openai"):
        _bench_settings(_pin(), paid=True)


def test_openai_requires_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    from capitalcite.eval.bench import _bench_settings
    from capitalcite.eval.runner import EvalUnverified as Unverified

    monkeypatch.setenv("ASK_MODEL", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(Unverified, match="ASK_MODEL=openai requires --paid"):
        _bench_settings(_pin(), paid=False)


async def test_full_corpus_missing_filings(tmp_path: Path) -> None:
    with pytest.raises(EvalUnverified, match=r"need 12 filings|missing raw/edgar"):
        async with indexed_corpus(tmp_path):
            pass
