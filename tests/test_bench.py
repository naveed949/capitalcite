from __future__ import annotations

from capitalcite.eval.bench_gate import (
    BenchBaseline,
    compare_bench,
    perturb_p95,
    snapshot_bench,
)
from capitalcite.eval.phases import fold_phases
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


def test_gate_passes_at_baseline_and_fails_when_p95_is_tripled() -> None:
    committed = BenchBaseline(
        n_items=95,
        model="fake",
        compile_once=True,
        p95_total_ms=40.0,
        mean_cost_usd=0.0,
    )
    assert compare_bench(committed, committed).ok is True
    blown = perturb_p95(committed, 3.0)
    report = compare_bench(blown, committed)
    assert report.ok is False
    assert any(row.metric == "p95_total_ms" for row in report.failures)


def test_gate_fails_on_positive_fake_cost() -> None:
    committed = BenchBaseline(
        n_items=95,
        model="fake",
        compile_once=True,
        p95_total_ms=40.0,
        mean_cost_usd=0.0,
    )
    current = committed.model_copy(update={"mean_cost_usd": 0.01})
    report = compare_bench(current, committed)
    assert report.ok is False
    assert any(row.metric == "mean_cost_usd" for row in report.failures)


def test_snapshot_bench_uses_graph_arm_p95() -> None:
    from capitalcite.eval.bench import BenchPin, BenchReport, QueryRecord
    from capitalcite.eval.phases import PhaseTimes
    from capitalcite.eval.prices import load_prices

    pin = BenchPin(
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

    def row(arm: str, total: float, accurate: bool) -> QueryRecord:
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
            recall_at_5=True,
            accurate=accurate,
        )

    report = BenchReport(
        git_sha="deadbeefdead",
        compile_once=True,
        pin=pin,
        prices=load_prices(),
        n_items=1,
        queries=(row("rag", 10, True), row("graph", 30, True), row("loop", 20, True)),
        identity_digest="b" * 64,
        recall_delta_graph_minus_loop=__import__(
            "capitalcite.eval.bootstrap", fromlist=["MeanCI"]
        ).MeanCI(point=0.0, lo=0.0, hi=0.0, B=10000, seed=20261103, n=1),
        accuracy_delta_graph_minus_loop=__import__(
            "capitalcite.eval.bootstrap", fromlist=["MeanCI"]
        ).MeanCI(point=0.0, lo=0.0, hi=0.0, B=10000, seed=20261103, n=1),
        recall_delta_graph_minus_rag=__import__(
            "capitalcite.eval.bootstrap", fromlist=["MeanCI"]
        ).MeanCI(point=0.0, lo=0.0, hi=0.0, B=10000, seed=20261103, n=1),
        accuracy_delta_graph_minus_rag=__import__(
            "capitalcite.eval.bootstrap", fromlist=["MeanCI"]
        ).MeanCI(point=0.0, lo=0.0, hi=0.0, B=10000, seed=20261103, n=1),
        bottleneck_phase="retrieval",
        estimated_paid_tokens_per_item={"tokens_in": 10.0, "tokens_out": 1.0},
    )
    snap = snapshot_bench(report)
    assert snap.p95_total_ms == 30.0
    assert snap.mean_cost_usd == 0.0
    assert snap.n_items == 1
