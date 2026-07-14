"""Own xG model.

Shot-based expected goals. With shot **coordinates** the per-shot xG is a
logistic function of distance and angle (``DataQuality.FULL``). football-data.co.uk
has no coordinates, so for historical seasons xG is **approximated** from shot
and shots-on-target counts (``DataQuality.APPROXIMATE``) — see the xG coverage
caveat in ``docs/DATA_SOURCES.md``. Wiring a shot-level source later needs only a
new provider that sets ``has_coordinates=True``; no rewrite.

Rolling xG/xGA over N matches regress to the league mean to stabilise small
samples.
"""

from __future__ import annotations

import math

from app.ml.base import DataQuality

# Logistic coefficients for the coordinate-based per-shot model.
_INTERCEPT = 0.85
_B_DISTANCE = -0.11  # per metre from goal
_B_ANGLE = 1.05  # per radian of goal-mouth angle

# Approximate per-shot conversion priors when only counts are available.
_XG_PER_SHOT = 0.09
_XG_PER_SHOT_ON_TARGET = 0.20


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class XgModel:
    def __init__(self, has_coordinates: bool = False) -> None:
        self._has_coordinates = has_coordinates

    @property
    def data_quality(self) -> DataQuality:
        return DataQuality.FULL if self._has_coordinates else DataQuality.APPROXIMATE

    def shot_xg(self, distance_m: float, angle_rad: float, is_header: bool = False) -> float:
        """Per-shot xG from geometry — always in (0, 1)."""
        logit = _INTERCEPT + _B_DISTANCE * distance_m + _B_ANGLE * angle_rad
        if is_header:
            logit -= 0.4
        return _sigmoid(logit)

    def approximate_match_xg(self, shots: int, shots_on_target: int) -> float:
        """Approximate a team's match xG from counts (no coordinates)."""
        off_target = max(shots - shots_on_target, 0)
        return off_target * _XG_PER_SHOT + shots_on_target * _XG_PER_SHOT_ON_TARGET

    @staticmethod
    def rolling_xg(
        recent_values: list[float], league_mean: float, regression_strength: float = 6.0
    ) -> float:
        """Rolling average with regression to the mean for small samples.

        estimate = (n * observed_mean + k * league_mean) / (n + k)
        """
        n = len(recent_values)
        if n == 0:
            return league_mean
        observed_mean = sum(recent_values) / n
        k = regression_strength
        return (n * observed_mean + k * league_mean) / (n + k)
