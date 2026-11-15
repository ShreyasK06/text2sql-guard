"""Confidence interval helpers: determinism, bounds, degenerate and empty inputs."""
import math

import numpy as np

from eval.stats import bootstrap_ci, wilson_ci


def test_bootstrap_deterministic():
    x = np.array([1] * 37 + [0] * 13)
    assert bootstrap_ci(x) == bootstrap_ci(x)


def test_bootstrap_bounds_and_rate():
    x = np.array([1] * 37 + [0] * 13)
    rate, lo, hi = bootstrap_ci(x)
    assert math.isclose(rate, 0.74)
    assert 0.0 <= lo <= rate <= hi <= 1.0
    assert hi - lo > 0.05


def test_bootstrap_interval_narrows_with_n():
    small = bootstrap_ci(np.array([1, 1, 0, 1] * 5))
    big = bootstrap_ci(np.array([1, 1, 0, 1] * 200))
    assert (big[2] - big[1]) < (small[2] - small[1])


def test_bootstrap_degenerate():
    assert bootstrap_ci(np.ones(20)) == (1.0, 1.0, 1.0)
    assert bootstrap_ci(np.zeros(20)) == (0.0, 0.0, 0.0)


def test_bootstrap_empty():
    r = bootstrap_ci(np.array([]))
    assert all(math.isnan(v) for v in r)


def test_bootstrap_accepts_list_and_bool():
    assert bootstrap_ci([True, False, True, True])[0] == 0.75


def test_wilson():
    rate, lo, hi = wilson_ci(8, 10)
    assert rate == 0.8
    assert 0.4 < lo < 0.8 < hi <= 1.0
    r0 = wilson_ci(0, 10)
    assert r0[0] == 0.0 and abs(r0[1]) < 1e-9 and r0[2] > 0.0
    assert all(math.isnan(v) for v in wilson_ci(0, 0))
