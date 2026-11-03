"""Nightly p95 latency and cost-per-query gate for the bench suite."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.atomic import atomic_write_text
from capitalcite.eval.baseline import CompareFailure, CompareReport
from capitalcite.eval.bench import BenchReport, summarize_arm
from capitalcite.eval.bootstrap import percentile

DEFAULT_BENCH_BASELINE = Path("evals/baselines/bench.json")

P95_LATENCY_MAX_RATIO = 2.0
COST_PER_QUERY_ABS = 0.0
TOLERANCE_RATIONALE = (
    "Fake-model p95 is wall-clock on a shared CPU runner. A 2x band absorbs "
    "scheduler noise and first-query index warmup without hiding a doubled "
    "retrieve or a compile-cache regression. Cost is exactly 0 on ASK_MODEL=fake; "
    "any positive mean fails."
)


class BenchThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)
    p95_latency_max_ratio: float = Field(gt=0.0, default=P95_LATENCY_MAX_RATIO)
    cost_per_query_abs: float = Field(ge=0.0, default=COST_PER_QUERY_ABS)
    rationale: str = TOLERANCE_RATIONALE


class BenchBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    n_items: int = Field(ge=1)
    model: str = Field(min_length=1)
    compile_once: bool
    p95_total_ms: float = Field(ge=0.0)
    mean_cost_usd: float = Field(ge=0.0)
    thresholds: BenchThresholds = Field(default_factory=BenchThresholds)


def snapshot_bench(report: BenchReport) -> BenchBaseline:
    graph = summarize_arm(report.queries, "graph")
    totals = [row.phases.total_ms for row in report.queries if row.arm == "graph"]
    return BenchBaseline(
        n_items=report.n_items,
        model=report.pin.model,
        compile_once=report.compile_once,
        p95_total_ms=percentile(totals, 0.95) if totals else 0.0,
        mean_cost_usd=graph.mean_cost_usd,
    )


def compare_bench(current: BenchBaseline, committed: BenchBaseline) -> CompareReport:
    failures: list[CompareFailure] = []
    if current.schema_version != committed.schema_version:
        failures.append(
            CompareFailure(
                metric="schema_version",
                detail=(
                    f"schema_version {current.schema_version} != "
                    f"committed {committed.schema_version}"
                ),
            )
        )
    if current.n_items != committed.n_items:
        failures.append(
            CompareFailure(
                metric="n_items",
                detail=f"{current.n_items} != committed {committed.n_items}",
            )
        )
    if current.model != committed.model:
        failures.append(
            CompareFailure(
                metric="model",
                detail=f"{current.model!r} != committed {committed.model!r}",
            )
        )
    if current.compile_once != committed.compile_once:
        failures.append(
            CompareFailure(
                metric="compile_once",
                detail=f"{current.compile_once} != committed {committed.compile_once}",
            )
        )
    limit = committed.p95_total_ms * committed.thresholds.p95_latency_max_ratio
    if current.p95_total_ms > limit:
        failures.append(
            CompareFailure(
                metric="p95_total_ms",
                detail=(
                    f"{current.p95_total_ms:.3f} > {limit:.3f} "
                    f"({committed.thresholds.p95_latency_max_ratio}x "
                    f"of {committed.p95_total_ms:.3f})"
                ),
            )
        )
    cost_limit = committed.mean_cost_usd + committed.thresholds.cost_per_query_abs
    if current.mean_cost_usd > cost_limit:
        failures.append(
            CompareFailure(
                metric="mean_cost_usd",
                detail=(
                    f"{current.mean_cost_usd:.6f} > {cost_limit:.6f} "
                    f"(abs {committed.thresholds.cost_per_query_abs})"
                ),
            )
        )
    return CompareReport(ok=not failures, failures=tuple(failures))


def load_bench_baseline(path: Path) -> BenchBaseline:
    return BenchBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_bench_baseline(path: Path, snapshot: BenchBaseline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, snapshot.model_dump_json(indent=2) + "\n")


def perturb_p95(snapshot: BenchBaseline, factor: float) -> BenchBaseline:
    return snapshot.model_copy(update={"p95_total_ms": snapshot.p95_total_ms * factor})
