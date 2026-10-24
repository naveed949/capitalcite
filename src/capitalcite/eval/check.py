"""PR regression pack: red-team, quote-index sanity, fixture recall, ask contracts."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field

from capitalcite.ask import (
    Answered,
    FakeStructuredModel,
    ModelNotConfigured,
    ModelUnavailable,
    Refused,
    ask,
    build_structured_model,
)
from capitalcite.chunking.models import ChunkerName
from capitalcite.eval.baseline import Baseline, CompareReport, TrackedRate, compare
from capitalcite.eval.fixture_index import (
    FIXTURE_RETRIEVE_K,
    FixtureIndexError,
    fixture_settings,
    indexed_fixture,
)
from capitalcite.eval.judge import recall_items, score_item
from capitalcite.eval.metrics_eval import run_metrics_suite
from capitalcite.eval.runner import run_suite
from capitalcite.gate.redteam import run_redteam
from capitalcite.golden.models import GoldenItem
from capitalcite.golden.store import ValidationError, load_items
from capitalcite.retrieval import RetrievalMode, RetrievalSetup, retrieve
from capitalcite.retrieval.sanity import SanityUnavailable, run_quote_index_sanity
from capitalcite.retrieval.types import Hit

_FIXTURE = Path("tests/fixtures/chunking/corpus")
_CI_ITEMS = Path("evals/golden/ci.jsonl")
_GOLDEN = Path("evals/golden")
DEFAULT_BASELINE = Path("evals/baselines/ci.json")
_UNANSWERABLE_ID = "cc-u-001"
_UNANSWERABLE_Q = "What CET1 ratio did Wells Fargo disclose for year-end 2026?"


class CheckFailed(Exception):
    """Deterministic pack cannot run. CLI exits 1."""


class ContractResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str = Field(min_length=1)
    expected: str
    observed: str
    ok: bool
    detail: str = ""


class LiveStatus(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: str
    detail: str = ""


class CheckReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot: Baseline
    redteam_passed: int = Field(ge=0)
    redteam_failed: int = Field(ge=0)
    contracts: tuple[ContractResult, ...]
    live: LiveStatus
    compare: CompareReport

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        if self.redteam_failed:
            return False
        if any(not case.ok for case in self.contracts):
            return False
        if self.live.kind == "failed":
            return False
        return self.compare.ok


async def run_check(
    *,
    committed: Baseline,
    live: bool,
    golden_dir: Path = _GOLDEN,
    ci_path: Path = _CI_ITEMS,
    fixture_root: Path = _FIXTURE,
) -> CheckReport:
    """Run the deterministic pack and compare it to the committed snapshot."""
    redteam = await run_redteam()
    try:
        sanity = await run_quote_index_sanity(golden_dir)
    except SanityUnavailable as exc:
        raise CheckFailed(str(exc)) from exc
    ci_items = _ci_items(ci_path)
    try:
        async with indexed_fixture(fixture_root) as setup:
            fixture_rate, hits_by_id = await _score_fixture(setup, ci_items)
            contracts = await _contracts(setup, ci_items, hits_by_id)
            live_status = await _live(live, setup, ci_items)
    except FixtureIndexError as exc:
        raise CheckFailed(str(exc)) from exc
    metrics = await run_metrics_suite(out_dir=None)
    with tempfile.TemporaryDirectory(prefix="capitalcite-check-small-") as tmp:
        small = await run_suite(
            suite="golden",
            small=True,
            input_dir=None,
            out_dir=Path(tmp),
            golden_dir=golden_dir,
            chunkers=(ChunkerName.SECTION, ChunkerName.FIXED),
            modes=(RetrievalMode.DENSE, RetrievalMode.BM25, RetrievalMode.HYBRID),
            embedder=None,
            k=5,
            settings=fixture_settings(),
            write=False,
        )
    snapshot = Baseline(
        schema_version=2,
        quote_sanity=TrackedRate(hits=sanity.hits, items=sanity.items),
        fixture=fixture_rate,
        metrics_agent_exact=metrics.agent.exact,
        metrics_agent_refusal=metrics.agent.refusal,
        metrics_rag_exact=metrics.rag.exact,
        metrics_rag_refusal=metrics.rag.refusal,
        metrics_v1_mart=TrackedRate(
            hits=metrics.coverage.hits,
            items=metrics.coverage.items,
        ),
        small_n_items=small.n_items,
        small_arms=len(small.arms),
        small_embedder_id=small.embedder_id,
        thresholds=committed.thresholds,
    )
    return CheckReport(
        snapshot=snapshot,
        redteam_passed=redteam.passed,
        redteam_failed=redteam.failed,
        contracts=contracts,
        live=live_status,
        compare=compare(snapshot, committed),
    )


def load_baseline(path: Path) -> Baseline:
    return Baseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_baseline(path: Path, snapshot: Baseline) -> None:
    from capitalcite.atomic import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, snapshot.model_dump_json(indent=2) + "\n")


async def _score_fixture(
    setup: RetrievalSetup, items: Sequence[GoldenItem]
) -> tuple[TrackedRate, dict[str, list[Hit]]]:
    scored = []
    hits_by_id: dict[str, list[Hit]] = {}
    for item in items:
        hits = await retrieve(setup, item.question, mode=RetrievalMode.HYBRID, k=FIXTURE_RETRIEVE_K)
        hits_by_id[item.id] = hits
        scored.append(score_item(item, hits))
    totals = TrackedRate(
        hits=sum(1 for row in scored if row.hit_at(5)),
        items=len(scored),
    )
    return totals, hits_by_id


async def _contracts(
    setup: RetrievalSetup,
    items: Sequence[GoldenItem],
    hits_by_id: dict[str, list[Hit]],
) -> tuple[ContractResult, ...]:
    by_id = {item.id: item for item in items}
    answered = by_id["cc-ci-002"]
    got = await ask(hits_by_id[answered.id], answered.question, FakeStructuredModel())
    un_hits = await retrieve(
        setup, _UNANSWERABLE_Q, mode=RetrievalMode.HYBRID, k=FIXTURE_RETRIEVE_K
    )
    refused = await ask(un_hits, _UNANSWERABLE_Q, FakeStructuredModel())
    return (
        _contract("cc-ci-002", "answered", got),
        _contract(_UNANSWERABLE_ID, "refused", refused),
    )


async def _live(live: bool, setup: RetrievalSetup, items: Sequence[GoldenItem]) -> LiveStatus:
    if not live:
        return LiveStatus(kind="skipped", detail="pass --live to call OpenAI")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return LiveStatus(kind="unverified", detail="OPENAI_API_KEY is empty")
    try:
        model = build_structured_model("openai", api_key=key, openai_model="gpt-4o-mini")
    except ModelNotConfigured as exc:
        return LiveStatus(kind="unverified", detail=str(exc))
    item = {row.id: row for row in items}["cc-ci-002"]
    try:
        hits = await retrieve(setup, item.question, mode=RetrievalMode.HYBRID, k=FIXTURE_RETRIEVE_K)
        got = await ask(hits, item.question, model)
    except ModelUnavailable as exc:
        return LiveStatus(kind="failed", detail=str(exc))
    if isinstance(got, Answered) and got.citations:
        return LiveStatus(kind="passed", detail="openai answered with citations")
    if isinstance(got, Refused):
        return LiveStatus(kind="failed", detail=got.refusal_reason)
    return LiveStatus(kind="failed", detail="openai returned no grounded citations")


def _contract(item_id: str, expected: str, got: Answered | Refused) -> ContractResult:
    observed = got.kind
    ok = observed == expected
    detail = ""
    if expected == "answered":
        if not isinstance(got, Answered) or not got.citations:
            ok = False
            detail = "expected grounded citations"
        else:
            detail = f"citations={len(got.citations)}"
    elif isinstance(got, Refused):
        detail = got.refusal_reason
    else:
        ok = False
        detail = "expected a refusal"
    return ContractResult(id=item_id, expected=expected, observed=observed, ok=ok, detail=detail)


def _ci_items(path: Path) -> tuple[GoldenItem, ...]:
    try:
        items = load_items(path)
    except (ValidationError, OSError) as exc:
        raise CheckFailed(str(exc)) from exc
    recall = recall_items(tuple(items))
    if len(recall) != len(items):
        raise CheckFailed(f"{path} has items without spanned evidence")
    if not recall:
        raise CheckFailed(f"{path} has no items")
    return recall
