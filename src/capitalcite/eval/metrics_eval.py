"""Score metric answers against a resolved cell, and count v1 rows the mart can answer."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field, model_validator

from capitalcite.ask.guard import tickers_named, years_named
from capitalcite.atomic import atomic_write_text
from capitalcite.config import Settings, metric_backends
from capitalcite.eval.baseline import TrackedRate
from capitalcite.eval.fixture_index import FixtureIndexError, indexed_fixture
from capitalcite.eval.metrics_gold import (
    DEFAULT_FIXTURE_ROOT,
    MetricGoldItem,
    Resolved,
    ResolvedValue,
    SourcePointer,
    load_metric_gold,
    resolve,
)
from capitalcite.eval.runner import EvalUnverified, _git_sha
from capitalcite.golden.models import GoldenItem, ItemSource, QuestionType
from capitalcite.golden.store import ValidationError as GoldenValidationError
from capitalcite.golden.store import load_items
from capitalcite.metric_query.query import (
    GroupBy,
    MetricName,
    MetricQuery,
    MetricQueryError,
    QueryBackend,
    locked_level,
    render_figure,
)
from capitalcite.retrieval import RetrievalSetup

if TYPE_CHECKING:
    from capitalcite.api.ask_models import AskResponse

LiteralPath = Literal["agent", "rag"]
LiteralCell = Literal["value", "null"]
LiteralOutcome = Literal["answered", "refused"]

DEFAULT_V1 = Path("evals/golden/v1.jsonl")
_FLOW = frozenset({MetricName.NET_INCOME, MetricName.HOLDING_COMPANY_NET_INCOME})
_PHRASES: tuple[tuple[str, MetricName], ...] = (
    ("holding company net income", MetricName.HOLDING_COMPANY_NET_INCOME),
    ("holding company deposits", MetricName.HOLDING_COMPANY_DEPOSITS),
    ("holding company assets", MetricName.HOLDING_COMPANY_ASSETS),
    ("net interest margin", MetricName.NIM),
    ("efficiency ratio", MetricName.EFFICIENCY_RATIO),
    ("deposit growth", MetricName.DEPOSITS_QOQ_GROWTH),
    ("total deposits", MetricName.TOTAL_DEPOSITS),
    ("total assets", MetricName.TOTAL_ASSETS),
    ("net income", MetricName.NET_INCOME),
)
_SCALE = re.compile(r"\s*(?:thousands?|millions?|billions?|trillions?)\b", re.IGNORECASE)
_COMPACT_QUARTER = re.compile(r"\b(\d{4})Q([1-4])\b", re.IGNORECASE)
_NAMED_QUARTER = re.compile(r"\bQ([1-4])\s+(\d{4})\b", re.IGNORECASE)
_ISO_QUARTER = re.compile(r"\b(\d{4})-(03-31|06-30|09-30|12-31)\b")
_MONTH_QUARTER = re.compile(
    r"\b(march|june|september|december)\s+(30|31),\s*(\d{4})\b",
    re.IGNORECASE,
)
_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_MONTH_DAY = {"march": (3, 31), "june": (6, 30), "september": (9, 30), "december": (12, 31)}


class MetricSuiteError(Exception):
    """The pack cannot run. A scored miss is not this."""


class ItemScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item_id: str
    path: LiteralPath
    cell: LiteralCell
    exact: bool
    refusal: bool
    outcome: LiteralOutcome
    source: SourcePointer
    rendered: str | None = None

    @model_validator(mode="after")
    def one_outcome(self) -> ItemScore:
        if self.cell == "value":
            if self.refusal:
                raise ValueError("a value cell is not a refusal")
            if self.exact and self.outcome != "answered":
                raise ValueError("exact requires an answered value cell")
            if self.rendered is None:
                raise ValueError("a value cell has a rendered figure")
            return self
        if self.exact or self.rendered is not None:
            raise ValueError("a null cell has no figure")
        if self.refusal and self.outcome != "refused":
            raise ValueError("refusal requires a refused null cell")
        return self


class PathRates(BaseModel):
    """exact.items is the value-cell count. refusal.items is the null-cell count."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    exact: TrackedRate
    refusal: TrackedRate


class CoverageClass(StrEnum):
    BANK = "BANK"
    CONCEPT = "CONCEPT"
    AMBIGUOUS = "AMBIGUOUS"
    GRAIN = "GRAIN"
    CELL = "CELL"
    NULL_CELL = "NULL_CELL"
    WAREHOUSE_ERROR = "WAREHOUSE_ERROR"


class V1Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item_id: str
    disposition: CoverageClass


class V1Coverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rows: tuple[V1Row, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hits(self) -> int:
        return sum(row.disposition is CoverageClass.CELL for row in self.rows)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def items(self) -> int:
        return len(self.rows)


class MetricsEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    suite: Literal["metrics"] = "metrics"
    git_sha: str = Field(min_length=1)
    scores: tuple[ItemScore, ...]
    coverage: V1Coverage

    @computed_field  # type: ignore[prop-decorator]
    @property
    def n(self) -> int:
        return len({row.item_id for row in self.scores})

    @computed_field  # type: ignore[prop-decorator]
    @property
    def agent(self) -> PathRates:
        return _path_rates(self.scores, "agent")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rag(self) -> PathRates:
        return _path_rates(self.scores, "rag")

    @model_validator(mode="after")
    def rates_partition_the_suite(self) -> MetricsEvalReport:
        ids = {row.item_id for row in self.scores}
        for path in ("agent", "rag"):
            rows = [row for row in self.scores if row.path == path]
            if len(rows) != len(ids) or {row.item_id for row in rows} != ids:
                raise ValueError("both paths score the same items once")
            rates = _path_rates(self.scores, path)
            if rates.exact.items + rates.refusal.items != len(ids):
                raise ValueError("exact and refusal denominators partition n")
        return self


def figure_token_in(rendered: str, text: str) -> bool:
    """True when rendered is one numeric token in text.

    A sentence period is a dot that does not continue into another digit.
    The metric quote ends the figure with one, and that period is still an edge.
    A scale word is a different quantity from the unscaled token.
    """
    if rendered == "":
        return False
    start = 0
    while True:
        index = text.find(rendered, start)
        if index < 0:
            return False
        end = index + len(rendered)
        bounded = _edge_before(text, index, rendered) and _edge_after(text, end)
        if bounded and not _scaled(text, end):
            return True
        start = index + 1


def score_answer(
    resolved: Resolved,
    response: AskResponse,
    path: LiteralPath,
    item: MetricGoldItem,
) -> ItemScore:
    """outcome is response.kind. Text is response.answer, or empty when refused."""
    outcome = response.kind
    text = response.answer or ""
    if isinstance(resolved, ResolvedValue):
        rendered = render_figure(resolved.value)
        exact = outcome == "answered" and figure_token_in(rendered, text)
        return ItemScore(
            item_id=item.id,
            path=path,
            cell="value",
            exact=exact,
            refusal=False,
            outcome=outcome,
            source=item.source,
            rendered=rendered,
        )
    return ItemScore(
        item_id=item.id,
        path=path,
        cell="null",
        exact=False,
        refusal=outcome == "refused",
        outcome=outcome,
        source=item.source,
        rendered=None,
    )


def cover_v1(
    v1_path: Path | None = None,
    *,
    backend: QueryBackend | None = None,
) -> V1Coverage:
    """One row per handwritten numeric item. hits counts non-null mart cells."""
    population = _handwritten_numeric(v1_path if v1_path is not None else DEFAULT_V1)
    rows = tuple(
        V1Row(item_id=item.id, disposition=_classify(item.question, backend)) for item in population
    )
    return V1Coverage(rows=rows)


async def run_metrics_suite(
    *,
    items_path: Path | None = None,
    out_dir: Path | None = None,
) -> MetricsEvalReport:
    """Ask both paths and write `<sha12>-metrics.json` when out_dir is set."""
    items = await load_metric_gold(items_path)
    # Importing the ask route builds the app, which requires a user agent.
    os.environ.setdefault("SEC_USER_AGENT", "capitalcite-eval eval@example.com")
    settings = _metrics_settings()
    with tempfile.TemporaryDirectory(prefix="capitalcite-metrics-") as tmp:
        asking = settings.model_copy(update={"audit_log_path": Path(tmp) / "audit.jsonl"})
        try:
            async with indexed_fixture() as setup:
                scores = await _score_paths(items, setup, asking)
        except FixtureIndexError as exc:
            raise MetricSuiteError(str(exc)) from exc
    try:
        backend, _citations = metric_backends(settings)
    except MetricQueryError as exc:
        raise MetricSuiteError(str(exc)) from exc
    if backend is None:
        raise EvalUnverified("warehouse is missing")
    report = MetricsEvalReport(
        git_sha=_git_sha(),
        scores=tuple(scores),
        coverage=cover_v1(backend=backend),
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = report.git_sha[:12] + "-metrics"
        atomic_write_text(out_dir / f"{stem}.json", report.model_dump_json(indent=2) + "\n")
    return report


def load_metrics_report(path: Path) -> MetricsEvalReport:
    try:
        report = MetricsEvalReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ValueError(str(exc)) from exc
    if report.suite != "metrics":
        raise ValueError("not a metrics report")
    return report


def render_metrics_markdown(report: MetricsEvalReport) -> str:
    agent = report.agent
    rag = report.rag
    summary = (
        f"suite=metrics sha={report.git_sha} n={report.n} "
        f"agent_exact={agent.exact.hits}/{agent.exact.items} "
        f"agent_refusal={agent.refusal.hits}/{agent.refusal.items} "
        f"rag_exact={rag.exact.hits}/{rag.exact.items} "
        f"rag_refusal={rag.refusal.hits}/{rag.refusal.items} "
        f"v1_mart={report.coverage.hits}/{report.coverage.items}"
    )
    return f"{summary}\n\n{_metrics_table(report)}"


def update_readme_metrics(readme: Path, report: MetricsEvalReport) -> None:
    text = readme.read_text(encoding="utf-8")
    if METRICS_BEGIN_MARK not in text or METRICS_END_MARK not in text:
        raise ValueError(f"{readme} is missing {METRICS_BEGIN_MARK} / {METRICS_END_MARK}")
    pre, rest = text.split(METRICS_BEGIN_MARK, 1)
    _, post = rest.split(METRICS_END_MARK, 1)
    block = f"{pre}{METRICS_BEGIN_MARK}\n\n{_metrics_table(report)}\n{METRICS_END_MARK}{post}"
    atomic_write_text(readme, block)


METRICS_BEGIN_MARK = "<!-- BEGIN METRICS EVAL -->"
METRICS_END_MARK = "<!-- END METRICS EVAL -->"


def _metrics_table(report: MetricsEvalReport) -> str:
    agent = report.agent
    rag = report.rag
    sha = report.git_sha[:12]
    lines = [
        "| Path | Model | n | Exact match | Correct refusals |",
        "|---|---|---:|---:|---:|",
        f"| agent | fake | {report.n} | {agent.exact.rate:.3f} | {agent.refusal.rate:.3f} |",
        f"| rag | fake | {report.n} | {rag.exact.rate:.3f} | {rag.refusal.rate:.3f} |",
        "",
        (
            f"Mart answers {report.coverage.hits} of {report.coverage.items} "
            "handwritten numeric v1 items."
        ),
        f"Generated from [`evals/results/{sha}-metrics.json`](evals/results/{sha}-metrics.json).",
        "",
    ]
    return "\n".join(lines)


def _metrics_settings() -> Settings:
    settings = Settings(sec_user_agent="capitalcite-eval eval@example.com")
    if settings.ask_model != "fake":
        raise EvalUnverified("ASK_MODEL must be fake")
    if settings.metric_query_backend != "catalog":
        raise EvalUnverified("METRIC_QUERY_BACKEND must be catalog")
    warehouse = settings.warehouse_path
    if warehouse is None or not warehouse.is_file():
        raise EvalUnverified("warehouse is missing")
    return settings


async def _score_paths(
    items: tuple[MetricGoldItem, ...],
    setup: RetrievalSetup,
    settings: Settings,
) -> list[ItemScore]:
    scores: list[ItemScore] = []
    paths: tuple[LiteralPath, ...] = ("agent", "rag")
    for item in items:
        resolved = resolve(item.source, DEFAULT_FIXTURE_ROOT)
        for path in paths:
            response = await _ask_response(item.question, path, setup, settings)
            scores.append(score_answer(resolved, response, path, item))
    return scores


async def _ask_response(
    question: str,
    path: LiteralPath,
    setup: RetrievalSetup,
    settings: Settings,
) -> AskResponse:
    from capitalcite.api.ask_models import AnsweredEvent, AskRequest, ErrorEvent, RefusedEvent
    from capitalcite.api.routes.ask import iter_ask

    response: AskResponse | None = None
    request = AskRequest(question=question, path=path)
    async for event in iter_ask(request, setup, settings):
        if isinstance(event, ErrorEvent):
            raise MetricSuiteError(event.detail)
        if isinstance(event, AnsweredEvent | RefusedEvent):
            response = event.response
    if response is None:
        raise MetricSuiteError("ask stream ended without a response")
    return response


def _path_rates(scores: tuple[ItemScore, ...], path: LiteralPath) -> PathRates:
    rows = [row for row in scores if row.path == path]
    values = [row for row in rows if row.cell == "value"]
    nulls = [row for row in rows if row.cell == "null"]
    return PathRates(
        exact=TrackedRate(hits=sum(row.exact for row in values), items=len(values)),
        refusal=TrackedRate(hits=sum(row.refusal for row in nulls), items=len(nulls)),
    )


def _handwritten_numeric(path: Path) -> tuple[GoldenItem, ...]:
    try:
        items = load_items(path)
    except (GoldenValidationError, OSError) as exc:
        raise MetricSuiteError(str(exc)) from exc
    return tuple(
        item
        for item in items
        if item.type is QuestionType.NUMERIC and item.source is ItemSource.HANDWRITTEN
    )


def _classify(question: str, backend: QueryBackend | None) -> CoverageClass:
    if len(tickers_named(question)) != 1:
        return CoverageClass.BANK
    metrics = _phrase_metrics(question)
    if not metrics:
        return CoverageClass.CONCEPT
    if len(metrics) != 1:
        return CoverageClass.AMBIGUOUS
    metric = metrics[0]
    quarters = _quarter_ends(question)
    years = years_named(question)
    if "average" in question.lower() or (metric in _FLOW and bool(years) and not quarters):
        return CoverageClass.GRAIN
    period = _one_period(quarters, years, metric)
    if period is None:
        return CoverageClass.GRAIN
    ticker = next(iter(tickers_named(question)))
    try:
        query = MetricQuery(
            metric=metric,
            group_by=GroupBy.QUARTER_AND_TICKER,
            ticker=ticker,
            period=period,
            entity_level=locked_level(metric),
        )
    except ValueError:
        return CoverageClass.WAREHOUSE_ERROR
    if backend is None:
        raise EvalUnverified("warehouse is missing")
    try:
        cell = backend.execute(query)
    except MetricQueryError as exc:
        if str(exc) == "warehouse is missing":
            raise EvalUnverified("warehouse is missing") from exc
        return CoverageClass.WAREHOUSE_ERROR
    if cell.value is None:
        return CoverageClass.NULL_CELL
    return CoverageClass.CELL


def _phrase_metrics(question: str) -> tuple[MetricName, ...]:
    lowered = question.lower()
    occupied: list[tuple[int, int]] = []
    found: list[MetricName] = []
    phrases = sorted(_PHRASES, key=lambda item: len(item[0]), reverse=True)
    for phrase, metric in phrases:
        start = 0
        while True:
            index = lowered.find(phrase, start)
            if index < 0:
                break
            end = index + len(phrase)
            if any(index < right and end > left for left, right in occupied):
                start = index + 1
                continue
            occupied.append((index, end))
            if metric not in found:
                found.append(metric)
            start = index + 1
    return tuple(found)


def _quarter_ends(question: str) -> tuple[date, ...]:
    found: list[date] = []

    def add(day: date) -> None:
        if day not in found:
            found.append(day)

    for match in _COMPACT_QUARTER.finditer(question):
        month, day_number = _QUARTER_END[int(match.group(2))]
        add(date(int(match.group(1)), month, day_number))
    for match in _NAMED_QUARTER.finditer(question):
        month, day_number = _QUARTER_END[int(match.group(1))]
        add(date(int(match.group(2)), month, day_number))
    for match in _ISO_QUARTER.finditer(question):
        add(date.fromisoformat(match.group(0)))
    for match in _MONTH_QUARTER.finditer(question):
        month, day_number = _MONTH_DAY[match.group(1).lower()]
        if int(match.group(2)) != day_number:
            continue
        add(date(int(match.group(3)), month, day_number))
    return tuple(found)


def _one_period(
    quarters: tuple[date, ...],
    years: set[int],
    metric: MetricName,
) -> date | None:
    if len(quarters) == 1:
        return quarters[0]
    if quarters or len(years) != 1 or metric in _FLOW:
        return None
    return date(next(iter(years)), 12, 31)


def _edge_before(text: str, index: int, rendered: str) -> bool:
    if index == 0:
        return True
    prev = text[index - 1]
    if prev.isdigit() or prev == ".":
        return False
    return prev != "-" or rendered.startswith("-")


def _edge_after(text: str, end: int) -> bool:
    if end >= len(text):
        return True
    nxt = text[end]
    if nxt == "%":
        return True
    if nxt.isdigit():
        return False
    if nxt == ".":
        return end + 1 >= len(text) or not text[end + 1].isdigit()
    return True


def _scaled(text: str, end: int) -> bool:
    return _SCALE.match(text, end) is not None
