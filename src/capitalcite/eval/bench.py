"""Agent vs RAG vs plain-loop bench. Fake model only unless a paid pin is used."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from capitalcite.agent import run_agent_loop, set_compile_once
from capitalcite.atomic import atomic_write_text
from capitalcite.config import Settings
from capitalcite.eval.bootstrap import MeanCI, paired_mean_ci, percentile
from capitalcite.eval.fixture_index import FixtureIndexError, indexed_corpus, indexed_fixture
from capitalcite.eval.judge import recall_items, score_item
from capitalcite.eval.metrics_eval import LiteralPath, score_answer
from capitalcite.eval.metrics_gold import (
    DEFAULT_FIXTURE_ROOT,
    MetricGoldItem,
    load_metric_gold,
    resolve,
)
from capitalcite.eval.phases import PhaseTimes, fold_phases
from capitalcite.eval.prices import DEFAULT_PRICES, PriceTable, cost_usd, load_prices
from capitalcite.eval.runner import EvalUnverified, git_revision
from capitalcite.eval.tokens import (
    TOKENIZER_NAME,
    agent_prompt_tokens,
    count_tokens,
    prompt_hash,
    rag_prompt_tokens,
)
from capitalcite.golden.models import GoldenItem
from capitalcite.golden.store import load_items
from capitalcite.retrieval import RetrievalMode, RetrievalSetup, retrieve
from capitalcite.retrieval.types import Hit
from capitalcite.trace import (
    MemoryExporter,
    ObservationKind,
    configure_for_tests,
    observation,
    reset_for_tests,
)

if TYPE_CHECKING:
    from capitalcite.api.ask_models import AskResponse

LiteralArm = Literal["rag", "graph", "loop"]
LiteralSet = Literal["v1_recall", "metrics"]
LiteralCorpus = Literal["fixture", "full"]
RECALL_NOT_MEASURED = "not measured (fixture index has no gold spans)"
DEFAULT_PIN = Path("evals/bench/pin.json")
DEFAULT_V1 = Path("evals/golden/v1.jsonl")
BOOTSTRAP_B = 10000
BOOTSTRAP_SEED = 20261103


class BenchSuiteError(Exception):
    """The bench cannot run. A scored miss is not this."""


class BenchPin(BaseModel):
    model_config = ConfigDict(frozen=True)
    suite: Literal["bench"] = "bench"
    n_items: int = Field(ge=1)
    sets: dict[str, int]
    model: Literal["fake", "openai"] = "fake"
    openai_model: str
    seed: int
    temperature: float
    tokenizer: str
    prompt_hash: str = Field(min_length=64, max_length=64)
    prices: str
    bootstrap_B: int = Field(ge=1)
    bootstrap_seed: int
    paid_run: dict[str, object]


class IdentityRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_id: str
    item_set: LiteralSet
    arm: LiteralArm
    kind: Literal["answered", "refused"]
    answer: str | None
    refusal_reason: str | None
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    tokens_in_api: int | None = None
    tokens_out_api: int | None = None
    cost_usd: float
    recall_at_5: bool | None
    accurate: bool


class QueryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_id: str
    item_set: LiteralSet
    arm: LiteralArm
    kind: Literal["answered", "refused"]
    answer: str | None = None
    refusal_reason: str | None = None
    phases: PhaseTimes
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    tokens_in_api: int | None = None
    tokens_out_api: int | None = None
    cost_usd: float
    recall_at_5: bool | None = None
    accurate: bool

    def identity(self) -> IdentityRow:
        return IdentityRow(
            item_id=self.item_id,
            item_set=self.item_set,
            arm=self.arm,
            kind=self.kind,
            answer=self.answer,
            refusal_reason=self.refusal_reason,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            tokens_in_api=self.tokens_in_api,
            tokens_out_api=self.tokens_out_api,
            cost_usd=self.cost_usd,
            recall_at_5=self.recall_at_5,
            accurate=self.accurate,
        )


class ArmSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    arm: LiteralArm
    n: int = Field(ge=0)
    recall_at_5: float
    recall_hits: int = Field(ge=0)
    recall_items: int = Field(ge=0)
    accuracy: float
    accurate: int = Field(ge=0)
    accuracy_items: int = Field(ge=0)
    p50_total_ms: float
    p95_total_ms: float
    mean_total_ms: float
    mean_ttft_ms: float
    mean_tool_ms: float
    mean_retrieval_ms: float
    mean_model_ms: float
    mean_compile_ms: float
    mean_tokens_in: float
    mean_tokens_out: float
    mean_cost_usd: float
    cost_per_correct: float | None


class RecallReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["measured", "not_measured"]
    reason: str
    graph_minus_loop: MeanCI | None = None
    graph_minus_rag: MeanCI | None = None

    @model_validator(mode="after")
    def measured_carries_ci(self) -> RecallReport:
        has_ci = self.graph_minus_loop is not None and self.graph_minus_rag is not None
        if self.status == "measured" and not has_ci:
            raise ValueError("measured recall needs paired CIs")
        if self.status == "not_measured" and (
            self.graph_minus_loop is not None or self.graph_minus_rag is not None
        ):
            raise ValueError("not_measured recall cannot carry a CI")
        return self


class BenchReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    suite: Literal["bench"] = "bench"
    git_sha: str = Field(min_length=1)
    compile_once: bool
    corpus: LiteralCorpus = "fixture"
    pin: BenchPin
    prices: PriceTable
    n_items: int = Field(ge=1)
    queries: tuple[QueryRecord, ...]
    identity_digest: str = Field(min_length=64, max_length=64)
    recall: RecallReport
    accuracy_delta_graph_minus_loop: MeanCI
    accuracy_delta_graph_minus_rag: MeanCI
    bottleneck_phase: str
    estimated_paid_tokens_per_item: dict[str, float]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def arms(self) -> dict[str, ArmSummary]:
        return {name: summarize_arm(self.queries, name) for name in ("rag", "graph", "loop")}

    @model_validator(mode="after")
    def three_arms_same_items(self) -> BenchReport:
        ids = {row.item_id for row in self.queries}
        if len(ids) != self.n_items:
            raise ValueError("n_items does not match distinct item ids")
        for arm in ("rag", "graph", "loop"):
            rows = [row for row in self.queries if row.arm == arm]
            if {row.item_id for row in rows} != ids:
                raise ValueError(f"{arm} is missing items")
        return self


def load_pin(path: Path | None = None) -> BenchPin:
    target = path if path is not None else DEFAULT_PIN
    pin = BenchPin.model_validate_json(target.read_text(encoding="utf-8"))
    live = prompt_hash()
    if pin.prompt_hash != live:
        raise BenchSuiteError(f"prompt hash {live} != pin {pin.prompt_hash}")
    if pin.tokenizer != TOKENIZER_NAME:
        raise BenchSuiteError(f"tokenizer {TOKENIZER_NAME} != pin {pin.tokenizer}")
    if pin.bootstrap_B != BOOTSTRAP_B or pin.bootstrap_seed != BOOTSTRAP_SEED:
        raise BenchSuiteError("bootstrap pin does not match BOOTSTRAP_B/SEED")
    return pin


def identity_digest(rows: Sequence[QueryRecord]) -> str:
    payload = [row.identity().model_dump(mode="json") for row in rows]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def summarize_arm(queries: Sequence[QueryRecord], arm: LiteralArm) -> ArmSummary:
    rows = [row for row in queries if row.arm == arm]
    recall_rows = [row for row in rows if row.recall_at_5 is not None]
    totals = [row.phases.total_ms for row in rows]
    accurate = sum(row.accurate for row in rows)
    recall_hits = sum(1 for row in recall_rows if row.recall_at_5)
    cost_sum = sum(row.cost_usd for row in rows)
    return ArmSummary(
        arm=arm,
        n=len(rows),
        recall_at_5=(recall_hits / len(recall_rows)) if recall_rows else 0.0,
        recall_hits=recall_hits,
        recall_items=len(recall_rows),
        accuracy=(accurate / len(rows)) if rows else 0.0,
        accurate=accurate,
        accuracy_items=len(rows),
        p50_total_ms=percentile(totals, 0.50) if totals else 0.0,
        p95_total_ms=percentile(totals, 0.95) if totals else 0.0,
        mean_total_ms=_mean(row.phases.total_ms for row in rows),
        mean_ttft_ms=_mean(row.phases.ttft_ms for row in rows),
        mean_tool_ms=_mean(row.phases.tool_ms for row in rows),
        mean_retrieval_ms=_mean(row.phases.retrieval_ms for row in rows),
        mean_model_ms=_mean(row.phases.model_ms for row in rows),
        mean_compile_ms=_mean(row.phases.compile_ms for row in rows),
        mean_tokens_in=_mean(float(row.tokens_in) for row in rows),
        mean_tokens_out=_mean(float(row.tokens_out) for row in rows),
        mean_cost_usd=_mean(row.cost_usd for row in rows),
        cost_per_correct=(cost_sum / accurate) if accurate else None,
    )


def bottleneck_phase(queries: Sequence[QueryRecord], arm: LiteralArm = "graph") -> str:
    summary = summarize_arm(queries, arm)
    phases = {
        "retrieval": summary.mean_retrieval_ms,
        "tool": summary.mean_tool_ms,
        "model": summary.mean_model_ms,
        "compile": summary.mean_compile_ms,
    }
    return max(phases, key=lambda name: phases[name])


async def run_bench_suite(
    *,
    out_dir: Path | None = None,
    compile_once: bool = True,
    pin_path: Path | None = None,
    stem_suffix: str = "-bench",
    corpus: LiteralCorpus = "fixture",
    paid: bool = False,
    input_dir: Path | None = None,
) -> BenchReport:
    """Ask rag, graph, and loop on the 77+18 item union."""
    os.environ.setdefault("SEC_USER_AGENT", "capitalcite-eval eval@example.com")
    pin = load_pin(pin_path)
    if paid:
        pin = pin.model_copy(update={"model": "openai"})
    prices = load_prices(Path(pin.prices) if pin.prices else DEFAULT_PRICES)
    settings = _bench_settings(pin, paid=paid)
    v1 = recall_items(load_items(DEFAULT_V1))
    metrics = await load_metric_gold()
    if len(v1) + len(metrics) != pin.n_items:
        raise BenchSuiteError(f"item count {len(v1)}+{len(metrics)} != pin {pin.n_items}")
    if pin.sets.get("v1_recall") != len(v1) or pin.sets.get("metrics") != len(metrics):
        raise BenchSuiteError("pin sets do not match loaded gold")
    set_compile_once(compile_once)
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    queries: list[QueryRecord] = []
    measure_recall = corpus == "full"
    try:
        with tempfile.TemporaryDirectory(prefix="capitalcite-bench-") as tmp:
            asking = settings.model_copy(update={"audit_log_path": Path(tmp) / "audit.jsonl"})
            try:
                async with _index_for(corpus, input_dir) as setup:
                    arms: tuple[LiteralArm, ...] = ("rag", "graph", "loop")
                    for gold in v1:
                        for arm in arms:
                            queries.append(
                                await _score_v1(
                                    gold,
                                    arm,
                                    setup,
                                    asking,
                                    exporter,
                                    prices,
                                    pin,
                                    measure_recall=measure_recall,
                                )
                            )
                    for metric in metrics:
                        for arm in arms:
                            queries.append(
                                await _score_metric(
                                    metric, arm, setup, asking, exporter, prices, pin
                                )
                            )
            except FixtureIndexError as exc:
                raise BenchSuiteError(str(exc)) from exc
    finally:
        reset_for_tests()
        set_compile_once(True)
    ordered = tuple(sorted(queries, key=lambda row: (row.item_id, row.arm)))
    if measure_recall:
        recall = RecallReport(
            status="measured",
            reason="twelve-filing section/hybrid index",
            graph_minus_loop=_delta(ordered, "recall_at_5", "graph", "loop"),
            graph_minus_rag=_delta(ordered, "recall_at_5", "graph", "rag"),
        )
    else:
        recall = RecallReport(status="not_measured", reason=RECALL_NOT_MEASURED)
    revision = git_revision()
    report = BenchReport(
        git_sha=revision,
        compile_once=compile_once,
        corpus=corpus,
        pin=pin,
        prices=prices,
        n_items=len(v1) + len(metrics),
        queries=ordered,
        identity_digest=identity_digest(ordered),
        recall=recall,
        accuracy_delta_graph_minus_loop=_delta(ordered, "accurate", "graph", "loop"),
        accuracy_delta_graph_minus_rag=_delta(ordered, "accurate", "graph", "rag"),
        bottleneck_phase=bottleneck_phase(ordered, "graph"),
        estimated_paid_tokens_per_item=_token_means(ordered),
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _result_stem(revision, stem_suffix)
        atomic_write_text(out_dir / f"{stem}.json", report.model_dump_json(indent=2) + "\n")
    return report


def _result_stem(revision: str, suffix: str) -> str:
    dirty = revision.endswith("-dirty")
    sha = revision.removesuffix("-dirty")[:12]
    marker = "-dirty" if dirty else ""
    return f"{sha}{marker}{suffix}"


@asynccontextmanager
async def _index_for(
    corpus: LiteralCorpus, input_dir: Path | None
) -> AsyncIterator[RetrievalSetup]:
    if corpus == "full":
        async with indexed_corpus(input_dir) as setup:
            yield setup
        return
    async with indexed_fixture() as setup:
        yield setup


def load_bench_report(path: Path) -> BenchReport:
    report = BenchReport.model_validate_json(path.read_text(encoding="utf-8"))
    if report.suite != "bench":
        raise ValueError("not a bench report")
    return report


def render_bench_markdown(report: BenchReport) -> str:
    lines = [
        f"suite=bench sha={report.git_sha} n={report.n_items} compile_once={report.compile_once}",
        f"identity={report.identity_digest}",
        f"bottleneck={report.bottleneck_phase}",
        "",
        "| Arm | n | Recall@5 | Accuracy | p50 ms | p95 ms | cost/query | cost/correct |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("rag", "graph", "loop"):
        arm = report.arms[name]
        cost_correct = "n/a" if arm.cost_per_correct is None else f"{arm.cost_per_correct:.6f}"
        recall_cell = _recall_cell(report, arm)
        lines.append(
            f"| {name} | {arm.n} | {recall_cell} | {arm.accuracy:.3f} | "
            f"{arm.p50_total_ms:.1f} | {arm.p95_total_ms:.1f} | "
            f"{arm.mean_cost_usd:.6f} | {cost_correct} |"
        )
    acc = report.accuracy_delta_graph_minus_loop
    lines.extend(
        [
            "",
            _recall_delta_line(report),
            (
                f"graph-loop accuracy delta={acc.point:.3f} "
                f"95% CI [{acc.lo:.3f}, {acc.hi:.3f}] B={acc.B} seed={acc.seed}"
            ),
            "Generated from a fake-model run. Paid OpenAI numbers are UNVERIFIED.",
            "",
        ]
    )
    return "\n".join(lines)


def _recall_cell(report: BenchReport, arm: ArmSummary) -> str:
    if report.recall.status != "measured":
        return "not measured"
    return f"{arm.recall_at_5:.3f}"


def _recall_delta_line(report: BenchReport) -> str:
    if report.recall.status != "measured" or report.recall.graph_minus_loop is None:
        return f"graph-loop Recall@5 delta={report.recall.reason}"
    rec = report.recall.graph_minus_loop
    return (
        f"graph-loop Recall@5 delta={rec.point:.3f} "
        f"95% CI [{rec.lo:.3f}, {rec.hi:.3f}] B={rec.B} seed={rec.seed}"
    )


BENCH_BEGIN_MARK = "<!-- BEGIN BENCH EVAL -->"
BENCH_END_MARK = "<!-- END BENCH EVAL -->"


def update_readme_bench(readme: Path, report: BenchReport) -> None:
    text = readme.read_text(encoding="utf-8")
    if BENCH_BEGIN_MARK not in text or BENCH_END_MARK not in text:
        raise ValueError(f"{readme} is missing {BENCH_BEGIN_MARK} / {BENCH_END_MARK}")
    pre, rest = text.split(BENCH_BEGIN_MARK, 1)
    _, post = rest.split(BENCH_END_MARK, 1)
    block = f"{pre}{BENCH_BEGIN_MARK}\n\n{_readme_table(report)}\n{BENCH_END_MARK}{post}"
    atomic_write_text(readme, block)


def _readme_table(report: BenchReport) -> str:
    sha = _result_stem(report.git_sha, "")
    lines = [
        "| Arm | n | Recall@5 | Accuracy | p50 ms | p95 ms | cost/query |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("rag", "graph", "loop"):
        arm = report.arms[name]
        recall_cell = _recall_cell(report, arm)
        lines.append(
            f"| {name} | {arm.n} | {recall_cell} | {arm.accuracy:.3f} | "
            f"{arm.p50_total_ms:.1f} | {arm.p95_total_ms:.1f} | {arm.mean_cost_usd:.6f} |"
        )
    acc = report.accuracy_delta_graph_minus_loop
    lines.extend(
        [
            "",
            _recall_delta_line(report),
            (f"graph-loop accuracy delta={acc.point:.3f} (95% CI {acc.lo:.3f} to {acc.hi:.3f})."),
            f"Bottleneck on the graph arm is **{report.bottleneck_phase}**.",
            f"Generated from [`evals/results/{sha}-bench.json`](evals/results/{sha}-bench.json).",
            "",
        ]
    )
    return "\n".join(lines)


async def _score_v1(
    item: GoldenItem,
    arm: LiteralArm,
    setup: RetrievalSetup,
    settings: Settings,
    exporter: MemoryExporter,
    prices: PriceTable,
    pin: BenchPin,
    *,
    measure_recall: bool,
) -> QueryRecord:
    response, hits, phases = await _run_arm(item.question, arm, setup, settings, exporter)
    recall = score_item(item, hits).hit_at(5) if measure_recall else None
    accurate = _v1_accurate(item, response)
    return _record(
        item.id,
        item.question,
        "v1_recall",
        arm,
        response,
        hits,
        phases,
        prices,
        pin,
        recall,
        accurate,
        exporter,
    )


async def _score_metric(
    item: MetricGoldItem,
    arm: LiteralArm,
    setup: RetrievalSetup,
    settings: Settings,
    exporter: MemoryExporter,
    prices: PriceTable,
    pin: BenchPin,
) -> QueryRecord:
    response, hits, phases = await _run_arm(item.question, arm, setup, settings, exporter)
    resolved = resolve(item.source, DEFAULT_FIXTURE_ROOT)
    path: LiteralPath = "rag" if arm == "rag" else "agent"
    score = score_answer(resolved, response, path, item)
    accurate = score.exact or score.refusal
    return _record(
        item.id,
        item.question,
        "metrics",
        arm,
        response,
        hits,
        phases,
        prices,
        pin,
        None,
        accurate,
        exporter,
    )


async def _run_arm(
    question: str,
    arm: LiteralArm,
    setup: RetrievalSetup,
    settings: Settings,
    exporter: MemoryExporter,
) -> tuple[AskResponse, tuple[Hit, ...], PhaseTimes]:
    exporter.clear()
    from capitalcite.api.ask_models import AnsweredEvent, AskRequest, ErrorEvent, RefusedEvent
    from capitalcite.api.routes.ask import _run_agent, iter_ask

    request = AskRequest(question=question, path="rag" if arm == "rag" else "agent")
    response: AskResponse | None = None
    hits: tuple[Hit, ...] = ()
    with observation("ask", ObservationKind.ASK):
        if arm == "rag":
            async for event in iter_ask(request, setup, settings):
                if isinstance(event, ErrorEvent):
                    raise BenchSuiteError(event.detail)
                if isinstance(event, AnsweredEvent | RefusedEvent):
                    response = event.response
        else:
            runner = run_agent_loop if arm == "loop" else None
            result = await _run_agent(request, setup, settings, runner=runner)
            hits = result.hits
            from capitalcite.api.ask_models import terminal_facts
            from capitalcite.api.routes.ask import _flatten

            for event in terminal_facts(_flatten(result.answer, setup, 0, path="agent")):
                if isinstance(event, AnsweredEvent | RefusedEvent):
                    response = event.response
    phases = fold_phases(exporter.finished(), exporter.begins())
    if arm == "rag":
        hits = tuple(await retrieve(setup, question, mode=RetrievalMode.HYBRID, k=setup.search_k))
    if response is None:
        raise BenchSuiteError("ask ended without a response")
    return response, hits, phases


def _record(
    item_id: str,
    question: str,
    item_set: LiteralSet,
    arm: LiteralArm,
    response: AskResponse,
    hits: Sequence[Hit],
    phases: PhaseTimes,
    prices: PriceTable,
    pin: BenchPin,
    recall_at_5: bool | None,
    accurate: bool,
    exporter: MemoryExporter,
) -> QueryRecord:
    traces = tuple(response.traces)
    synthesized = response.kind == "answered" or bool(hits)
    if arm == "rag":
        tokens_in = rag_prompt_tokens(question, hits)
    else:
        tokens_in = agent_prompt_tokens(
            question,
            traces,
            hits,
            synthesized=synthesized,
        )
    text = response.answer or response.refusal_reason or ""
    tokens_out = count_tokens(text)
    tokens_in_api, tokens_out_api = _api_tokens(exporter)
    billed = pin.model != "fake"
    return QueryRecord(
        item_id=item_id,
        item_set=item_set,
        arm=arm,
        kind=response.kind,
        answer=response.answer,
        refusal_reason=response.refusal_reason,
        phases=phases,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_in_api=tokens_in_api,
        tokens_out_api=tokens_out_api,
        cost_usd=cost_usd(
            prices,
            model_id=pin.openai_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            billed=billed,
        ),
        recall_at_5=recall_at_5,
        accurate=accurate,
    )


def _v1_accurate(item: GoldenItem, response: AskResponse) -> bool:
    if not item.answerable:
        return response.kind == "refused"
    if response.kind != "answered":
        return False
    text = (response.answer or "").lower()
    gold = item.answer.strip().lower()
    return bool(gold) and gold in text


def _delta(
    queries: Sequence[QueryRecord],
    field: Literal["recall_at_5", "accurate"],
    left: LiteralArm,
    right: LiteralArm,
) -> MeanCI:
    by_left = {row.item_id: row for row in queries if row.arm == left}
    by_right = {row.item_id: row for row in queries if row.arm == right}
    deltas: list[float] = []
    for item_id, left_row in by_left.items():
        right_row = by_right[item_id]
        left_val = getattr(left_row, field)
        right_val = getattr(right_row, field)
        if left_val is None or right_val is None:
            continue
        deltas.append(float(left_val) - float(right_val))
    return paired_mean_ci(deltas, B=BOOTSTRAP_B, seed=BOOTSTRAP_SEED)


def _token_means(queries: Sequence[QueryRecord]) -> dict[str, float]:
    rows = [row for row in queries if row.arm == "graph"]
    if not rows:
        return {"tokens_in": 0.0, "tokens_out": 0.0}
    return {
        "tokens_in": _mean(float(row.tokens_in) for row in rows),
        "tokens_out": _mean(float(row.tokens_out) for row in rows),
    }


def _mean(values: Iterable[float]) -> float:
    rows = list(values)
    if not rows:
        return 0.0
    return sum(rows) / len(rows)


def _api_tokens(exporter: MemoryExporter) -> tuple[int | None, int | None]:
    inn = 0
    out = 0
    found = False
    for span in exporter.finished():
        attrs = span.attributes
        raw_in = attrs.get("gen_ai.usage.input_tokens")
        raw_out = attrs.get("gen_ai.usage.output_tokens")
        if isinstance(raw_in, int):
            inn += raw_in
            found = True
        if isinstance(raw_out, int):
            out += raw_out
            found = True
    if not found:
        return None, None
    return inn, out


def _bench_settings(pin: BenchPin, *, paid: bool) -> Settings:
    settings = Settings(sec_user_agent="capitalcite-eval eval@example.com")
    if settings.ask_model == "openai" and not paid:
        raise EvalUnverified("ASK_MODEL=openai requires --paid")
    if paid and settings.ask_model != "openai":
        raise EvalUnverified("--paid requires ASK_MODEL=openai")
    if settings.ask_model not in {"fake", "openai"}:
        raise EvalUnverified("ASK_MODEL must be fake or openai")
    if settings.ask_model == "openai":
        key = (settings.openai_api_key or "").strip()
        if not key:
            raise EvalUnverified("ASK_MODEL=openai requires OPENAI_API_KEY")
        settings = settings.model_copy(
            update={"ask_seed": pin.seed, "ask_temperature": pin.temperature}
        )
    if settings.metric_query_backend != "catalog":
        raise EvalUnverified("METRIC_QUERY_BACKEND must be catalog")
    warehouse = settings.warehouse_path
    if warehouse is None or not warehouse.is_file():
        raise EvalUnverified("warehouse is missing")
    return settings
