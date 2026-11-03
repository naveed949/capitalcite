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


def test_paired_bootstrap_fifty_zeros_and_fifty_ones() -> None:
    deltas = [0.0] * 50 + [1.0] * 50
    ci = paired_mean_ci(deltas, B=10000, seed=20261103)
    assert ci.point == 0.5
    assert ci.B == 10000
    assert ci.seed == 20261103
    assert ci.n == 100
    assert 0.39 <= ci.lo <= 0.41
    assert 0.59 <= ci.hi <= 0.61


def test_pairing_narrower_when_left_differs_from_right() -> None:
    left = [0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0] * 6
    right = [0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0] * 6
    assert left != right
    deltas = [a - b for a, b in zip(left, right, strict=True)]
    paired = paired_mean_ci(deltas, B=10000, seed=20261103)
    unpaired = unpaired_mean_diff_ci(left, right, B=10000, seed=20261103)
    assert paired.point != 0.0
    assert paired.hi - paired.lo < unpaired.hi - unpaired.lo
