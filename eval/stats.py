"""Confidence intervals for block rates. Deterministic (seeded)."""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

NAN3 = (float("nan"), float("nan"), float("nan"))


def bootstrap_ci(successes, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """Percentile bootstrap CI for the mean of a 0/1 array.

    Resampling n Bernoulli draws with replacement from a 0/1 sample is exactly
    Binomial(n, p_hat) / n, so we draw that directly (identical distribution, no n_boot x n
    matrix). Returns (rate, lo, hi); (nan, nan, nan) for empty input. All-0 and all-1
    samples give a degenerate interval [rate, rate].
    """
    arr = np.asarray(successes, dtype=float).ravel()
    n = int(arr.size)
    if n == 0:
        return NAN3
    p = float(arr.mean())
    if p in (0.0, 1.0):
        return p, p, p
    rng = np.random.default_rng(seed)
    boots = rng.binomial(n, p, size=n_boot) / n
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return p, float(lo), float(hi)


def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float, float]:
    """Wilson score interval for a binomial proportion. Returns (rate, lo, hi)."""
    if n <= 0:
        return NAN3
    z = NormalDist().inv_cdf(1 - alpha / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)
