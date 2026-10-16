"""Measure decide() overhead. No network. No tool runners."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.agent.tools import ToolRegistry
from capitalcite.gate.gate import Gate
from capitalcite.gate.policy import Policy, load_policy


class BenchReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    n: int = Field(ge=1)
    p50_ms: float
    p95_ms: float
    allow_p95_ms: float
    deny_p95_ms: float


def bench_decide(n: int = 2000) -> BenchReport:
    if n < 2:
        raise ValueError("n must be at least 2")
    half = n // 2
    gate = Gate(_unlimited_calls(), ToolRegistry({}))
    allow_samples = [_time_decide(gate, "calculator", {"expression": "1+1"}) for _ in range(half)]
    deny_samples = [_time_decide(gate, "shell", {"cmd": "ls"}) for _ in range(n - half)]
    all_samples = allow_samples + deny_samples
    return BenchReport(
        n=len(all_samples),
        p50_ms=_percentile(all_samples, 50),
        p95_ms=_percentile(all_samples, 95),
        allow_p95_ms=_percentile(allow_samples, 95),
        deny_p95_ms=_percentile(deny_samples, 95),
    )


def _unlimited_calls() -> Policy:
    loaded = load_policy(None)
    if not isinstance(loaded, Policy):
        raise ValueError(f"shipped policy failed to load: {loaded.detail}")
    tools = {
        name: rule.model_copy(update={"max_calls": 100_000})
        for name, rule in loaded.tools.items()
    }
    return Policy(version=1, default="deny", tools=tools)


def _time_decide(gate: Gate, name: str, args: dict[str, object]) -> float:
    started = time.perf_counter()
    gate.decide(name, args)
    return (time.perf_counter() - started) * 1000.0


def _percentile(samples: list[float], pct: Literal[50, 95]) -> float:
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("no samples")
    rank = max(1, int((pct / 100) * len(ordered)))
    return ordered[rank - 1]
