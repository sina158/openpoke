"""Small statistics for the eval report. Stdlib only."""

from __future__ import annotations

import random
from math import comb, sqrt
from typing import Dict, List, Tuple


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for k successes out of n (95% by default)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def rule_of_three(n: int) -> float:
    """95% upper bound on a rate when 0 events were seen in n trials."""
    return 1.0 if n == 0 else 3 / n


def pass_k(outcomes_by_case: Dict[str, List[bool]]) -> float:
    """Share of cases that passed on every trial (pass^k)."""
    if not outcomes_by_case:
        return 0.0
    return sum(all(trials) for trials in outcomes_by_case.values()) / len(outcomes_by_case)


def agreement(outcomes_by_case: Dict[str, List[bool]]) -> float:
    """Share of cases whose trials all agree (all pass or all fail)."""
    if not outcomes_by_case:
        return 0.0
    return sum(len(set(trials)) == 1 for trials in outcomes_by_case.values()) / len(outcomes_by_case)


def cluster_bootstrap(values_by_cluster: Dict[str, List[float]], resamples: int = 2000, seed: int = 0,
                      level: float = 0.95) -> Tuple[float, float]:
    """Percentile interval for the pooled mean, resampling whole clusters (e.g. templates) with replacement.

    Cases built from one template are not independent, so this interval is wider than Wilson's when
    templates differ a lot.
    """
    clusters = [(sum(values), len(values)) for _, values in sorted(values_by_cluster.items()) if values]
    if not clusters:
        return 0.0, 1.0
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        total = count = 0
        for _ in range(len(clusters)):
            cluster_total, cluster_count = clusters[rng.randrange(len(clusters))]
            total += cluster_total
            count += cluster_count
        means.append(total / count)
    means.sort()
    tail = (1 - level) / 2
    return means[int(tail * resamples)], means[min(resamples - 1, int((1 - tail) * resamples))]
