"""Same-run graph/rag p95 ratio and cost-per-query gate for the bench suite."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.atomic import atomic_write_text
from capitalcite.eval.baseline import CompareFailure, CompareReport
from capitalcite.eval.bench import BenchReport, summarize_arm

DEFAULT_BENCH_BASELINE = Path("evals/baselines/bench.json")

GRAPH_OVER_RAG_MAX_RATIO = 2.0
COST_PER_QUERY_ABS = 0.0
COMPILE_MS_MAX = 0.5
TOLERANCE_RATIONALE = (
    "Absolute graph p95 is wall-clock on the host. A loaded runner measured "
    "96-100 ms against a 38.8 ms laptop snapshot and failed a 2x ms band. "
    "The gate compares graph p95 to rag p95 in the same run. Both arms see "
    "the same scheduler. A 2x band on that ratio absorbs first-query warmup. "
    "It does not catch a compile-cache miss: a --no-compile-once run still "
    "sits inside the band. When compile_once is true, mean graph compile_ms "
    "must stay under 0.5. Cost is exactly 0 on ASK_MODEL=fake; any positive "
    "mean fails."
)


class BenchThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)
    graph_over_rag_p95_max_ratio: float = Field(gt=0.0, default=GRAPH_OVER_RAG_MAX_RATIO)
    cost_per_query_abs: float = Field(ge=0.0, default=COST_PER_QUERY_ABS)
    compile_ms_max: float = Field(gt=0.0, default=COMPILE_MS_MAX)
    rationale: str = TOLERANCE_RATIONALE


class BenchBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = 2
    n_items: int = Field(ge=1)
    model: str = Field(min_length=1)
    compile_once: bool
    graph_over_rag_p95: float = Field(ge=0.0)
    graph_p95_total_ms: float = Field(ge=0.0)
    rag_p95_total_ms: float = Field(ge=0.0)
    mean_cost_usd: float = Field(ge=0.0)
    mean_compile_ms: float = Field(ge=0.0)
    thresholds: BenchThresholds = Field(default_factory=BenchThresholds)


def _ratio(graph_p95: float, rag_p95: float) -> float:
    floor = max(rag_p95, 1.0)
    return graph_p95 / floor


def snapshot_bench(report: BenchReport) -> BenchBaseline:
    graph = summarize_arm(report.queries, "graph")
    rag = summarize_arm(report.queries, "rag")
    return BenchBaseline(
        n_items=report.n_items,
        model=report.pin.model,
        compile_once=report.compile_once,
        graph_over_rag_p95=_ratio(graph.p95_total_ms, rag.p95_total_ms),
        graph_p95_total_ms=graph.p95_total_ms,
        rag_p95_total_ms=rag.p95_total_ms,
        mean_cost_usd=graph.mean_cost_usd,
        mean_compile_ms=graph.mean_compile_ms,
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
    limit = committed.graph_over_rag_p95 * committed.thresholds.graph_over_rag_p95_max_ratio
    if current.graph_over_rag_p95 > limit:
        failures.append(
            CompareFailure(
                metric="graph_over_rag_p95",
                detail=(
                    f"{current.graph_over_rag_p95:.3f} > {limit:.3f} "
                    f"({committed.thresholds.graph_over_rag_p95_max_ratio}x "
                    f"of {committed.graph_over_rag_p95:.3f})"
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
    compile_limit = committed.thresholds.compile_ms_max
    if current.compile_once and current.mean_compile_ms >= compile_limit:
        failures.append(
            CompareFailure(
                metric="mean_compile_ms",
                detail=(f"{current.mean_compile_ms:.3f} >= {compile_limit} (compile_once=true)"),
            )
        )
    return CompareReport(ok=not failures, failures=tuple(failures))


def load_bench_baseline(path: Path) -> BenchBaseline:
    return BenchBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_bench_baseline(path: Path, snapshot: BenchBaseline) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, snapshot.model_dump_json(indent=2) + "\n")


def perturb_p95(snapshot: BenchBaseline, factor: float) -> BenchBaseline:
    graph_p95 = snapshot.graph_p95_total_ms * factor
    return snapshot.model_copy(
        update={
            "graph_p95_total_ms": graph_p95,
            "graph_over_rag_p95": _ratio(graph_p95, snapshot.rag_p95_total_ms),
        }
    )
