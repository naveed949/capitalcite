from __future__ import annotations

from capitalcite.eval.bootstrap import paired_mean_ci, unpaired_mean_diff_ci


def test_paired_bootstrap_constant_delta_is_exact() -> None:
    deltas = [0.25] * 20
    ci = paired_mean_ci(deltas, B=10000, seed=20261103)
    assert ci.point == 0.25
    assert ci.lo == 0.25
    assert ci.hi == 0.25
    assert ci.B == 10000
    assert ci.seed == 20261103
    assert ci.n == 20


def test_paired_bootstrap_known_all_ones() -> None:
    ci = paired_mean_ci([1.0, 1.0, 1.0, 1.0], B=2000, seed=0)
    assert ci.point == 1.0
    assert ci.lo == 1.0
    assert ci.hi == 1.0


def test_pairing_cuts_variance_on_correlated_items() -> None:
    left = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
    right = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
    deltas = [a - b for a, b in zip(left, right, strict=True)]
    paired = paired_mean_ci(deltas, B=4000, seed=20261103)
    unpaired = unpaired_mean_diff_ci(left, right, B=4000, seed=20261103)
    assert paired.point == 0.0
    assert unpaired.point == 0.0
    assert paired.hi - paired.lo < unpaired.hi - unpaired.lo
