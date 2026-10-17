"""Committed CI snapshot and the comparison that fails a PR.

Contracts and gate red-team are pass/fail. Retrieval rates compare as
item counts so a 1/n move at small n is visible and a 1% relative drop
is not the rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field

from capitalcite.eval.models import EvalReport

DEFAULT_NIGHTLY = Path("evals/baselines/nightly.json")


class TrackedRate(BaseModel):
    """hits/items for one gated retrieval number."""

    model_config = ConfigDict(frozen=True)
    hits: int = Field(ge=0)
    items: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rate(self) -> float:
        if self.items == 0:
            return 0.0
        return self.hits / self.items


class Thresholds(BaseModel):
    """Allowed item drops below the committed snapshot.

    The PR pack is FakeEmbedder plus a fixture index. That path is
    deterministic, so both budgets default to zero. Nightly live-corpus
    rates use NightlyThresholds.max_drop_items instead.
    """

    model_config = ConfigDict(frozen=True)
    quote_sanity_max_drop_items: int = Field(ge=0, default=0)
    fixture_max_drop_items: int = Field(ge=0, default=0)


class Baseline(BaseModel):
    """evals/baselines/ci.json. Rates only. Red-team and contracts are absolute."""

    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    quote_sanity: TrackedRate
    fixture: TrackedRate
    small_n_items: int = Field(ge=0)
    small_arms: int = Field(ge=0)
    small_embedder_id: str = Field(min_length=1)
    thresholds: Thresholds = Field(default_factory=Thresholds)


class CompareFailure(BaseModel):
    model_config = ConfigDict(frozen=True)
    metric: str
    detail: str


class CompareReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool
    failures: tuple[CompareFailure, ...]


def compare(current: Baseline, committed: Baseline) -> CompareReport:
    """Fail when a tracked rate drops more than max_drop_items, or smoke fields move."""
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
    failures.extend(_smoke(current, committed))
    failures.extend(
        _rate(
            "quote_sanity",
            current.quote_sanity,
            committed.quote_sanity,
            committed.thresholds.quote_sanity_max_drop_items,
        )
    )
    failures.extend(
        _rate(
            "fixture",
            current.fixture,
            committed.fixture,
            committed.thresholds.fixture_max_drop_items,
        )
    )
    return CompareReport(ok=not failures, failures=tuple(failures))


def _smoke(current: Baseline, committed: Baseline) -> list[CompareFailure]:
    out: list[CompareFailure] = []
    if current.small_n_items != committed.small_n_items:
        out.append(
            CompareFailure(
                metric="small_n_items",
                detail=f"{current.small_n_items} != committed {committed.small_n_items}",
            )
        )
    if current.small_arms != committed.small_arms:
        out.append(
            CompareFailure(
                metric="small_arms",
                detail=f"{current.small_arms} != committed {committed.small_arms}",
            )
        )
    if current.small_embedder_id != committed.small_embedder_id:
        out.append(
            CompareFailure(
                metric="small_embedder_id",
                detail=(
                    f"{current.small_embedder_id!r} != committed {committed.small_embedder_id!r}"
                ),
            )
        )
    return out


def _rate(
    metric: str,
    current: TrackedRate,
    committed: TrackedRate,
    max_drop_items: int,
) -> Sequence[CompareFailure]:
    if current.items != committed.items:
        return (
            CompareFailure(
                metric=metric,
                detail=f"items {current.items} != committed {committed.items}",
            ),
        )
    drop = committed.hits - current.hits
    if drop > max_drop_items:
        return (
            CompareFailure(
                metric=metric,
                detail=(
                    f"hits {current.hits}/{current.items} dropped {drop} "
                    f"from {committed.hits}/{committed.items} "
                    f"(max_drop_items={max_drop_items})"
                ),
            ),
        )
    return ()


class NightlyArm(BaseModel):
    """One (chunker, mode) Recall@5 cell from the twelve-filing matrix."""

    model_config = ConfigDict(frozen=True)
    chunker: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    recall_at_5: TrackedRate


class NightlyThresholds(BaseModel):
    """Item budget for the live 12-K matrix. Rank-boundary noise at n=77."""

    model_config = ConfigDict(frozen=True)
    max_drop_items: int = Field(ge=0, default=1)


class NightlyBaseline(BaseModel):
    """evals/baselines/nightly.json. Nightly CI commits one arm (section/hybrid)."""

    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    n_items: int = Field(ge=0)
    embedder_id: str = Field(min_length=1)
    sparse_id: str = ""
    arms: tuple[NightlyArm, ...]
    thresholds: NightlyThresholds = Field(default_factory=NightlyThresholds)


def snapshot_nightly(
    report: EvalReport,
    *,
    thresholds: NightlyThresholds | None = None,
) -> NightlyBaseline:
    """Pack Recall@5 hit counts from a full-suite EvalReport."""
    if report.small:
        raise ValueError("nightly baseline is the twelve-filing matrix, not --small")
    return NightlyBaseline(
        n_items=report.n_items,
        embedder_id=report.embedder_id,
        sparse_id=report.sparse_id,
        arms=tuple(
            NightlyArm(
                chunker=arm.chunker.value,
                mode=arm.mode.value,
                recall_at_5=TrackedRate(
                    hits=arm.metrics.items - len(arm.metrics.misses_at_5),
                    items=arm.metrics.items,
                ),
            )
            for arm in report.arms
        ),
        thresholds=thresholds if thresholds is not None else NightlyThresholds(),
    )


def compare_nightly(current: NightlyBaseline, committed: NightlyBaseline) -> CompareReport:
    """Fail when an arm drops more than max_drop_items, or identity fields move."""
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
    if current.embedder_id != committed.embedder_id:
        failures.append(
            CompareFailure(
                metric="embedder_id",
                detail=f"{current.embedder_id!r} != committed {committed.embedder_id!r}",
            )
        )
    if current.sparse_id != committed.sparse_id:
        failures.append(
            CompareFailure(
                metric="sparse_id",
                detail=f"{current.sparse_id!r} != committed {committed.sparse_id!r}",
            )
        )
    committed_by = {(arm.chunker, arm.mode): arm for arm in committed.arms}
    current_by = {(arm.chunker, arm.mode): arm for arm in current.arms}
    if set(current_by) != set(committed_by):
        failures.append(
            CompareFailure(
                metric="arms",
                detail=f"arms {sorted(current_by)!r} != committed {sorted(committed_by)!r}",
            )
        )
        return CompareReport(ok=False, failures=tuple(failures))
    budget = committed.thresholds.max_drop_items
    for key, committed_arm in committed_by.items():
        metric = f"{key[0]}/{key[1]}/recall_at_5"
        failures.extend(
            _rate(metric, current_by[key].recall_at_5, committed_arm.recall_at_5, budget)
        )
    return CompareReport(ok=not failures, failures=tuple(failures))


def load_nightly(path: Path) -> NightlyBaseline:
    return NightlyBaseline.model_validate_json(path.read_text(encoding="utf-8"))


def write_nightly(path: Path, snapshot: NightlyBaseline) -> None:
    from capitalcite.atomic import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, snapshot.model_dump_json(indent=2) + "\n")
