"""Agent vs RAG vs plain-loop bench. Fake model only unless a paid pin is used."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from capitalcite.agent import run_agent_loop, set_compile_once
from capitalcite.ask.endpoint import ChatEndpoint, chat_endpoint_for, host_class_for
from capitalcite.ask.types import AskModelName, ModelNotConfigured
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
from capitalcite.eval.prices import DEFAULT_PRICES, PriceTable, bench_cost_usd, load_prices
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
    host_class: Literal["localhost", "openai", "other"] | None = None
    openai_model: str
    seed: int
    temperature: float
    tokenizer: str
    prompt_hash: str = Field(min_length=64, max_length=64)
    prices: str
    bootstrap_B: int = Field(ge=1)
    bootstrap_seed: int
    paid_run: dict[str, object]

    @model_validator(mode="after")
    def paid_run_has_no_url(self) -> BenchPin:
        _reject_url_stamp(self.paid_run, where="paid_run")
        return self


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
    recall_at_5: float | None
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


class BenchProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["fake", "local", "hosted"]
    host_class: Literal["none", "localhost", "openai", "other"]
    model_id: str = Field(min_length=1)
    pin_name: str = Field(min_length=1)
    comparable_to_hosted: bool
    latency_scope: Literal["process_fake", "host_hardware", "provider_api"]

    @model_validator(mode="after")
    def one_story(self) -> BenchProvenance:
        if "://" in self.model_id or "://" in self.pin_name:
            raise ValueError("provenance must not contain a URL")
        if "/" in self.pin_name or self.pin_name in {".", ".."}:
            raise ValueError("pin_name is a file name")
        if (
            self.provider == "fake"
            and self.host_class == "none"
            and self.model_id == "fake"
            and not self.comparable_to_hosted
            and self.latency_scope == "process_fake"
        ):
            return self
        if (
            self.provider == "local"
            and self.host_class in {"localhost", "other"}
            and not self.comparable_to_hosted
            and self.latency_scope == "host_hardware"
        ):
            return self
        if (
            self.provider == "hosted"
            and self.host_class == "openai"
            and self.comparable_to_hosted
            and self.latency_scope == "provider_api"
        ):
            return self
        raise ValueError("provenance does not match one run story")


_FAKE_PROVENANCE = {
    "provider": "fake",
    "host_class": "none",
    "model_id": "fake",
    "pin_name": "pin.json",
    "comparable_to_hosted": False,
    "latency_scope": "process_fake",
}


def _legacy_provenance() -> BenchProvenance:
    return BenchProvenance.model_validate(dict(_FAKE_PROVENANCE))


class BenchReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    suite: Literal["bench"] = "bench"
    git_sha: str = Field(min_length=1)
    compile_once: bool
    corpus: LiteralCorpus = "fixture"
    pin: BenchPin
    prices: PriceTable
    provenance: BenchProvenance = Field(default_factory=_legacy_provenance)
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
        recall_at_5=(recall_hits / len(recall_rows)) if recall_rows else None,
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
    corpus: LiteralCorpus = "fixture",
    paid: bool = False,
    input_dir: Path | None = None,
) -> BenchReport:
    """Ask rag, graph, and loop on the 77+18 item union."""
    os.environ.setdefault("SEC_USER_AGENT", "capitalcite-eval eval@example.com")
    ready = resolve_bench_run(pin_path, paid=paid)
    pin = ready.pin
    prices = ready.prices
    settings = ready.settings
    provenance = ready.provenance
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
                                    provenance,
                                    measure_recall=measure_recall,
                                )
                            )
                    for metric in metrics:
                        for arm in arms:
                            queries.append(
                                await _score_metric(
                                    metric,
                                    arm,
                                    setup,
                                    asking,
                                    exporter,
                                    prices,
                                    pin,
                                    provenance,
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
        provenance=provenance,
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
        suffix = bench_stem_suffix(compile_once=compile_once, provenance=provenance)
        stem = _result_stem(revision, suffix)
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
            provenance_sentence(report.provenance),
            "",
        ]
    )
    return "\n".join(lines)


def _recall_cell(report: BenchReport, arm: ArmSummary) -> str:
    if report.recall.status != "measured" or arm.recall_at_5 is None:
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
    suffix = bench_stem_suffix(compile_once=report.compile_once, provenance=report.provenance)
    stem = _result_stem(report.git_sha, suffix)
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
            f"Generated from [`evals/results/{stem}.json`](evals/results/{stem}.json).",
            provenance_sentence(report.provenance),
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
    provenance: BenchProvenance,
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
        provenance,
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
    provenance: BenchProvenance,
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
        provenance,
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
    provenance: BenchProvenance,
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
        cost_usd=bench_cost_usd(
            prices,
            provenance,
            model_id=pin.openai_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
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


class _BenchReady(NamedTuple):
    pin: BenchPin
    prices: PriceTable
    settings: Settings
    endpoint: ChatEndpoint | None
    provenance: BenchProvenance


def resolve_bench_run(pin_path: Path | None, *, paid: bool) -> _BenchReady:
    path = pin_path if pin_path is not None else DEFAULT_PIN
    pin = load_pin(path)
    if paid:
        pin = pin.model_copy(update={"model": "openai"})
    return _ready(pin, path, paid=paid)


def _bench_settings(pin: BenchPin, *, paid: bool) -> _BenchReady:
    return _ready(pin, DEFAULT_PIN, paid=paid)


def _ready(pin: BenchPin, pin_path: Path, *, paid: bool) -> _BenchReady:
    prices = load_prices(Path(pin.prices) if pin.prices else DEFAULT_PRICES)
    settings, endpoint = _open_bench(pin, paid=paid)
    provenance = bench_provenance(endpoint, pin, pin_path)
    return _BenchReady(pin, prices, settings, endpoint, provenance)


def _open_bench(pin: BenchPin, *, paid: bool) -> tuple[Settings, ChatEndpoint | None]:
    settings = Settings(sec_user_agent="capitalcite-eval eval@example.com")
    if settings.ask_model not in {"fake", "openai"}:
        raise EvalUnverified("ASK_MODEL must be fake or openai")
    endpoint: ChatEndpoint | None
    if settings.ask_model == "fake":
        if pin.host_class is not None:
            raise EvalUnverified("pin host_class requires ASK_MODEL=openai")
        gate_paid(None, paid=paid, ask_model="fake")
        endpoint = None
    else:
        settings = settings.model_copy(
            update={
                "ask_openai_model": pin.openai_model,
                "ask_seed": pin.seed,
                "ask_temperature": pin.temperature,
            }
        )
        actual = host_class_for(settings.ask_openai_base_url)
        _require_pin_host(pin, actual)
        if actual == "openai" and not paid:
            raise EvalUnverified("hosted OpenAI requires --paid")
        if actual != "openai" and paid:
            raise EvalUnverified("--paid does not apply to a local chat endpoint")
        try:
            endpoint = chat_endpoint_for(settings, "openai")
        except ModelNotConfigured as exc:
            raise EvalUnverified(str(exc)) from exc
        gate_paid(endpoint, paid=paid, ask_model=settings.ask_model)
    if settings.metric_query_backend != "catalog":
        raise EvalUnverified("METRIC_QUERY_BACKEND must be catalog")
    warehouse = settings.warehouse_path
    if warehouse is None or not warehouse.is_file():
        raise EvalUnverified("warehouse is missing")
    return settings, endpoint


def _require_pin_host(pin: BenchPin, host_class: str) -> None:
    recorded = pin.host_class
    if recorded is None:
        if host_class != "openai":
            raise EvalUnverified("a local chat endpoint requires pin host_class")
        return
    if recorded != host_class:
        raise EvalUnverified(
            f"pin host_class {recorded} does not match chat host class {host_class}"
        )


def gate_paid(
    endpoint: ChatEndpoint | None,
    *,
    paid: bool,
    ask_model: AskModelName,
) -> None:
    if ask_model == "fake":
        if paid:
            raise EvalUnverified("--paid requires ASK_MODEL=openai")
        return
    if endpoint is None:
        raise EvalUnverified("ASK_MODEL=openai requires OPENAI_API_KEY")
    if endpoint.label.billed:
        if not paid:
            raise EvalUnverified("hosted OpenAI requires --paid")
        return
    if paid:
        raise EvalUnverified("--paid does not apply to a local chat endpoint")


def bench_provenance(
    endpoint: ChatEndpoint | None,
    pin: BenchPin,
    pin_path: Path,
) -> BenchProvenance:
    name = pin_path.name
    if endpoint is None:
        return BenchProvenance(
            provider="fake",
            host_class="none",
            model_id="fake",
            pin_name=name,
            comparable_to_hosted=False,
            latency_scope="process_fake",
        )
    label = endpoint.label
    if label.model_id != pin.openai_model:
        raise ValueError("endpoint model does not match the pin")
    if label.provider == "local":
        return BenchProvenance(
            provider="local",
            host_class=label.host_class,
            model_id=label.model_id,
            pin_name=name,
            comparable_to_hosted=False,
            latency_scope="host_hardware",
        )
    return BenchProvenance(
        provider="hosted",
        host_class="openai",
        model_id=label.model_id,
        pin_name=name,
        comparable_to_hosted=True,
        latency_scope="provider_api",
    )


def bench_stem_suffix(*, compile_once: bool, provenance: BenchProvenance) -> str:
    base = "-bench" if compile_once else "-bench-before"
    if provenance.provider != "local":
        return base
    if provenance.host_class == "localhost":
        return f"{base}-localhost"
    if provenance.host_class == "other":
        return f"{base}-other"
    raise ValueError("local provenance needs host class localhost or other")


def baseline_refusal(provenance: BenchProvenance) -> str | None:
    if provenance.provider == "fake":
        return None
    return "--baseline and --write-baseline require a fake-model run"


def provenance_sentence(provenance: BenchProvenance) -> str:
    if provenance.provider == "fake":
        return "Generated from a fake-model run. Paid OpenAI numbers are UNVERIFIED."
    named = (
        f"provider {provenance.provider}, host class {provenance.host_class}, "
        f"model {provenance.model_id}, and pin {provenance.pin_name}"
    )
    if provenance.provider == "local":
        return (
            f"This run used {named}. It is not comparable to hosted gpt-4o-mini. "
            "Latency is the machine that served the model."
        )
    return f"This run used {named}."


def _reject_url_stamp(value: object, *, where: str) -> None:
    if isinstance(value, str):
        if "://" in value:
            raise ValueError(f"{where} must not contain a URL")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_url_stamp(key, where=where)
            _reject_url_stamp(item, where=where)
        return
    if isinstance(value, list):
        for item in value:
            _reject_url_stamp(item, where=where)
