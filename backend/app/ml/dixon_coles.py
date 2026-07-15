"""Dixon-Coles bivariate Poisson.

Attack/defence strengths + a low-score correlation correction (rho) + time
decay (applied during fitting). Produces the full score matrix, from which 1X2,
any total, and per-half totals are derived. This is the totals engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.ml.base import Outcome, Probs1x2

MAX_GOALS = 10


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lam_home: float, lam_away: float, rho: float, max_goals: int = MAX_GOALS
) -> np.ndarray:
    """Normalized P(home=i, away=j) matrix; rows=home goals, cols=away goals."""
    home_pmf = np.array([_poisson_pmf(i, lam_home) for i in range(max_goals + 1)])
    away_pmf = np.array([_poisson_pmf(j, lam_away) for j in range(max_goals + 1)])
    matrix = np.outer(home_pmf, away_pmf)
    for i in (0, 1):
        for j in (0, 1):
            matrix[i, j] *= _tau(i, j, lam_home, lam_away, rho)
    total = matrix.sum()
    normalized: np.ndarray = matrix / total
    return normalized


def one_x_two(matrix: np.ndarray) -> Probs1x2:
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    return {Outcome.home: home, Outcome.draw: draw, Outcome.away: away}


def total_over_under(matrix: np.ndarray, line: float) -> dict[str, float]:
    size = matrix.shape[0]
    over = 0.0
    under = 0.0
    for i in range(size):
        for j in range(size):
            if i + j > line:
                over += matrix[i, j]
            else:
                under += matrix[i, j]
    return {"over": float(over), "under": float(under)}


def in_play_score_matrix(
    lam_home: float,
    lam_away: float,
    rho: float,
    minute: int,
    match_minutes: int = 90,
    max_goals: int = MAX_GOALS,
) -> np.ndarray:
    """Score matrix for the goals *remaining* in the match.

    In-play, only the unplayed fraction of the match is still uncertain: the
    pre-match rates are scaled by the share of time left, so at the final
    whistle the remaining-goals distribution collapses to a certain 0-0 and the
    outcome is fully determined by the current score.
    """
    remaining = max(match_minutes - minute, 0) / match_minutes
    return score_matrix(lam_home * remaining, lam_away * remaining, rho, max_goals)


def in_play_one_x_two(
    lam_home: float,
    lam_away: float,
    rho: float,
    home_score: int,
    away_score: int,
    minute: int,
    match_minutes: int = 90,
) -> Probs1x2:
    """1X2 probabilities given the current score and elapsed minute.

    Convolves the current (certain) score with the distribution of the goals
    still to come.
    """
    matrix = in_play_score_matrix(lam_home, lam_away, rho, minute, match_minutes)
    size = matrix.shape[0]
    home = draw = away = 0.0
    for i in range(size):
        for j in range(size):
            final_home = home_score + i
            final_away = away_score + j
            prob = float(matrix[i, j])
            if final_home > final_away:
                home += prob
            elif final_home == final_away:
                draw += prob
            else:
                away += prob
    return {Outcome.home: home, Outcome.draw: draw, Outcome.away: away}


@dataclass(slots=True)
class DixonColesParams:
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    home_adv: float = 0.25
    rho: float = -0.05
    # Fraction of goals scored in the first half (empirically < 0.5).
    first_half_fraction: float = 0.45


class DixonColes:
    def __init__(self, params: DixonColesParams | None = None) -> None:
        self.params = params or DixonColesParams()

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        p = self.params
        atk_h = p.attack.get(home, 0.0)
        atk_a = p.attack.get(away, 0.0)
        def_h = p.defence.get(home, 0.0)
        def_a = p.defence.get(away, 0.0)
        lam_home = math.exp(atk_h - def_a + p.home_adv)
        lam_away = math.exp(atk_a - def_h)
        return lam_home, lam_away

    def score_matrix(self, home: str, away: str) -> np.ndarray:
        lam_home, lam_away = self.expected_goals(home, away)
        return score_matrix(lam_home, lam_away, self.params.rho)

    def predict_1x2(self, home: str, away: str) -> Probs1x2:
        return one_x_two(self.score_matrix(home, away))

    def predict_total(self, home: str, away: str, line: float = 2.5) -> dict[str, float]:
        return total_over_under(self.score_matrix(home, away), line)

    def predict_half_total(
        self, home: str, away: str, half: str = "first", line: float = 1.5
    ) -> dict[str, float]:
        lam_home, lam_away = self.expected_goals(home, away)
        frac = self.params.first_half_fraction
        if half == "second":
            frac = 1.0 - frac
        matrix = score_matrix(lam_home * frac, lam_away * frac, self.params.rho)
        return total_over_under(matrix, line)
