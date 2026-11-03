"""Dated price table. Fake-model cost is always 0. Local rates are exactly 0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PRICES = Path("evals/bench/prices/openai-2026-11-03.json")
PriceBilling = Literal["hosted", "local"]


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)
    input_usd_per_1m: float = Field(ge=0.0)
    output_usd_per_1m: float = Field(ge=0.0)


class PriceTable(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    retrieved: str = Field(min_length=1)
    label: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    billing: PriceBilling = "hosted"
    models: dict[str, ModelPrice]


class _CostRun(Protocol):
    @property
    def provider(self) -> Literal["fake", "local", "hosted"]: ...


def load_prices(path: Path | None = None) -> PriceTable:
    target = path if path is not None else DEFAULT_PRICES
    return PriceTable.model_validate(json.loads(target.read_text(encoding="utf-8")))


def cost_usd(
    table: PriceTable,
    *,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    billed: bool,
) -> float:
    """Return 0 when the run is not billed. Otherwise list-price tokens."""
    if not billed:
        return 0.0
    price = table.models.get(model_id)
    if price is None:
        raise ValueError(f"no list price for {model_id!r} in {table.id}")
    return (tokens_in * price.input_usd_per_1m + tokens_out * price.output_usd_per_1m) / 1_000_000


def require_local_zero(table: PriceTable, model_id: str) -> None:
    if table.billing != "local":
        raise ValueError(f"local run requires billing local in {table.id}")
    price = table.models.get(model_id)
    if price is None:
        raise ValueError(f"no local price for {model_id!r} in {table.id}")
    if price.input_usd_per_1m != 0 or price.output_usd_per_1m != 0:
        raise ValueError(f"local rates for {model_id!r} must be 0")


def bench_cost_usd(
    table: PriceTable,
    provenance: _CostRun,
    *,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
) -> float:
    if provenance.provider == "fake":
        return 0.0
    if provenance.provider == "local":
        require_local_zero(table, model_id)
        return 0.0
    if table.billing != "hosted":
        raise ValueError(f"hosted run requires billing hosted in {table.id}")
    return cost_usd(
        table,
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        billed=True,
    )
