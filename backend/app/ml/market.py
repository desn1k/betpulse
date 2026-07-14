"""Market-implied probabilities via Shin's method.

Removes the bookmaker margin by modelling the proportion of insider ("informed")
money, rather than naively normalizing 1/odds. This is the benchmark line to
beat — we report our edge against it, never treat it as a model.
"""

from __future__ import annotations


def _shin_probs(pi: list[float], booksum: float, z: float) -> list[float]:
    denom = 2.0 * (1.0 - z)
    return [(((z**2 + 4.0 * (1.0 - z) * (p**2) / booksum) ** 0.5) - z) / denom for p in pi]


def shin_devig(odds: list[float]) -> list[float]:
    """De-vig decimal odds into probabilities that sum to 1 (all > 0).

    Solves for the insider-trading proportion ``z`` by bisection so the returned
    probabilities sum to exactly 1.
    """
    if len(odds) < 2 or any(o <= 1.0 for o in odds):
        raise ValueError("need at least two decimal odds each > 1.0")

    pi = [1.0 / o for o in odds]
    booksum = sum(pi)

    lo, hi = 0.0, 0.999
    for _ in range(200):
        z = (lo + hi) / 2.0
        s = sum(_shin_probs(pi, booksum, z))
        if s > 1.0:
            lo = z  # too much probability → need more insider adjustment
        else:
            hi = z
        if abs(s - 1.0) < 1e-12:
            break

    probs = _shin_probs(pi, booksum, (lo + hi) / 2.0)
    total = sum(probs)
    return [p / total for p in probs]
