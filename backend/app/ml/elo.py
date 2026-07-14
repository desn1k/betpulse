"""Football-adapted Elo.

Goal-difference-weighted rating updates with a home-advantage term. Updates are
zero-sum (the winner gains exactly what the loser loses), so rating mass is
conserved; the home-advantage term only shifts the *expected* result, not the
magnitude of the exchange. Rating history is persisted to ``ratings_elo`` by the
training pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ml.base import Outcome, Probs1x2

DEFAULT_RATING = 1500.0


def _gd_multiplier(goal_diff: int) -> float:
    """World-Football-Elo style margin-of-victory multiplier."""
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11.0 + gd) / 8.0


@dataclass(frozen=True, slots=True)
class EloConfig:
    k: float = 20.0
    home_advantage: float = 65.0
    # Spread of the logistic expectation curve (Elo standard: 400).
    scale: float = 400.0
    # Draw peak used to split the win expectation into 1X2.
    draw_scale: float = 0.28


class Elo:
    def __init__(self, config: EloConfig | None = None) -> None:
        self.config = config or EloConfig()

    def expected_home(self, home_rating: float, away_rating: float) -> float:
        diff = (home_rating + self.config.home_advantage) - away_rating
        return float(1.0 / (1.0 + 10.0 ** (-diff / self.config.scale)))

    def update(
        self, home_rating: float, away_rating: float, home_goals: int, away_goals: int
    ) -> tuple[float, float]:
        expected = self.expected_home(home_rating, away_rating)
        if home_goals > away_goals:
            actual = 1.0
        elif home_goals < away_goals:
            actual = 0.0
        else:
            actual = 0.5
        k_eff = self.config.k * _gd_multiplier(home_goals - away_goals)
        delta = k_eff * (actual - expected)
        return home_rating + delta, away_rating - delta

    def prob_1x2(self, home_rating: float, away_rating: float) -> Probs1x2:
        """Split the win expectation into home/draw/away.

        Draw probability peaks when the two sides are evenly matched and decays
        as the rating gap widens; the remaining mass is allocated to home/away in
        proportion to the Elo win expectation.
        """
        expected = self.expected_home(home_rating, away_rating)
        draw = self.config.draw_scale * (1.0 - abs(2.0 * expected - 1.0))
        home = (1.0 - draw) * expected
        away = (1.0 - draw) * (1.0 - expected)
        return {Outcome.home: home, Outcome.draw: draw, Outcome.away: away}
