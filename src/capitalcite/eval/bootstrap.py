"""Paired bootstrap mean and a percentile that names its interpolation."""

from __future__ import annotations

import random
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field


class MeanCI(BaseModel):
    model_config = ConfigDict(frozen=True)
    point: float
    lo: float
    hi: float
    B: int = Field(ge=1)
    seed: int
    n: int = Field(ge=0)


def percentile(values: Sequence[float], p: float) -> float:
    """Linear interpolation between adjacent order statistics.

    ``p`` is in ``[0, 1]``. With ``n`` samples the rank is ``(n - 1) * p``.
    This is Hyndman-Fan type 7. At n=20, p95 sits between the 19th and 20th
    order statistic, not at a single sample. Small n makes that gap wide.
    """
    if not values:
        raise ValueError("percentile needs at least one value")
    if p < 0.0 or p > 1.0:
        raise ValueError("p must be in [0, 1]")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def paired_mean_ci(deltas: Sequence[float], *, B: int, seed: int) -> MeanCI:
    """Resample per-item paired differences. Pairing keeps the shared item."""
    if B < 1:
        raise ValueError("B must be at least 1")
    rows = tuple(float(value) for value in deltas)
    n = len(rows)
    if n == 0:
        raise ValueError("paired bootstrap needs at least one pair")
    point = sum(rows) / n
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(B):
        acc = 0.0
        for _index in range(n):
            acc += rows[rng.randrange(n)]
        means.append(acc / n)
    return MeanCI(
        point=point,
        lo=percentile(means, 0.025),
        hi=percentile(means, 0.975),
        B=B,
        seed=seed,
        n=n,
    )


def unpaired_mean_diff_ci(
    left: Sequence[float],
    right: Sequence[float],
    *,
    B: int,
    seed: int,
) -> MeanCI:
    """Resample each arm independently. Wider than paired when items correlate."""
    if B < 1:
        raise ValueError("B must be at least 1")
    a = tuple(float(value) for value in left)
    b = tuple(float(value) for value in right)
    if not a or not b:
        raise ValueError("unpaired bootstrap needs both arms")
    point = (sum(a) / len(a)) - (sum(b) / len(b))
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(B):
        left_mean = sum(a[rng.randrange(len(a))] for _i in range(len(a))) / len(a)
        right_mean = sum(b[rng.randrange(len(b))] for _i in range(len(b))) / len(b)
        diffs.append(left_mean - right_mean)
    return MeanCI(
        point=point,
        lo=percentile(diffs, 0.025),
        hi=percentile(diffs, 0.975),
        B=B,
        seed=seed,
        n=min(len(a), len(b)),
    )
