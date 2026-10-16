"""Committed CI snapshot and the comparison that fails a PR.

Contracts and gate red-team are pass/fail. Retrieval rates compare as
item counts so a 1/n move at small n is visible and a 1% relative drop
is not the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, computed_field


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
    """Allowed item drops below the committed snapshot. Zero for pass/fail suites."""

    model_config = ConfigDict(frozen=True)
    quote_sanity_max_drop_items: int = Field(ge=0, default=1)
    fixture_max_drop_items: int = Field(ge=0, default=1)


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
