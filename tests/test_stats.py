"""Known values for the eval statistics."""

import pytest

from evals.stats import agreement, mcnemar_exact, pass_k, rule_of_three, wilson


def test_wilson_known_values():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = wilson(5, 10)
    assert (lo, hi) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))
    lo, hi = wilson(10, 10)
    assert lo == pytest.approx(0.7225, abs=1e-4) and hi == 1.0
    assert wilson(0, 0) == (0.0, 1.0)


def test_mcnemar_exact_known_values():
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(1, 9) == pytest.approx(0.021484375)


def test_rule_of_three():
    assert rule_of_three(100) == pytest.approx(0.03)
    assert rule_of_three(0) == 1.0


def test_pass_k_and_agreement():
    outcomes = {"a": [True, True, True], "b": [True, False, True], "c": [False, False, False]}
    assert pass_k(outcomes) == pytest.approx(1 / 3)
    assert agreement(outcomes) == pytest.approx(2 / 3)
    assert pass_k({}) == 0.0 and agreement({}) == 0.0


def test_cluster_bootstrap():
    from evals.stats import cluster_bootstrap

    same = {f"t{i}": [1.0, 0.0] for i in range(10)}
    assert cluster_bootstrap(same) == (0.5, 0.5)
    split = {"a": [1.0] * 10, "b": [0.0] * 10, "c": [1.0] * 10, "d": [0.0] * 10}
    lo, hi = cluster_bootstrap(split, seed=1)
    assert lo < 0.3 and hi > 0.7
    assert cluster_bootstrap(split, seed=1) == (lo, hi)
    assert cluster_bootstrap({}) == (0.0, 1.0)
