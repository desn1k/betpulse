"""Glicko-2 rating system.

Carries a rating deviation (RD) that widens with inactivity and narrows after
matches, exposing confidence (promoted / post-break teams get wider intervals).
Implemented per Glickman's Glicko-2 paper. Persisted to ``ratings_glicko``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06


@dataclass(frozen=True, slots=True)
class GlickoPlayer:
    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    volatility: float = DEFAULT_VOL


@dataclass(frozen=True, slots=True)
class MatchResult:
    opponent_rating: float
    opponent_rd: float
    score: float  # 1 win, 0.5 draw, 0 loss


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi**2 / math.pi**2)


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


class Glicko2:
    def __init__(self, tau: float = 0.5) -> None:
        self.tau = tau

    def apply_inactivity(self, player: GlickoPlayer) -> GlickoPlayer:
        """No matches this period → RD grows by the volatility step."""
        phi = player.rd / _SCALE
        phi_star = math.sqrt(phi**2 + player.volatility**2)
        return GlickoPlayer(player.rating, phi_star * _SCALE, player.volatility)

    def update(self, player: GlickoPlayer, results: list[MatchResult]) -> GlickoPlayer:
        if not results:
            return self.apply_inactivity(player)

        mu = (player.rating - DEFAULT_RATING) / _SCALE
        phi = player.rd / _SCALE
        sigma = player.volatility

        opps = [
            (
                (r.opponent_rating - DEFAULT_RATING) / _SCALE,
                r.opponent_rd / _SCALE,
                r.score,
            )
            for r in results
        ]

        v_inv = sum(
            _g(phi_j) ** 2 * _e(mu, mu_j, phi_j) * (1 - _e(mu, mu_j, phi_j))
            for mu_j, phi_j, _ in opps
        )
        v = 1.0 / v_inv
        delta = v * sum(_g(phi_j) * (s - _e(mu, mu_j, phi_j)) for mu_j, phi_j, s in opps)

        sigma_prime = self._new_volatility(phi, sigma, v, delta)
        phi_star = math.sqrt(phi**2 + sigma_prime**2)
        phi_prime = 1.0 / math.sqrt(1.0 / phi_star**2 + 1.0 / v)
        mu_prime = mu + phi_prime**2 * sum(
            _g(phi_j) * (s - _e(mu, mu_j, phi_j)) for mu_j, phi_j, s in opps
        )

        return GlickoPlayer(
            rating=mu_prime * _SCALE + DEFAULT_RATING,
            rd=phi_prime * _SCALE,
            volatility=sigma_prime,
        )

    def _new_volatility(self, phi: float, sigma: float, v: float, delta: float) -> float:
        a = math.log(sigma**2)
        tau = self.tau

        def f(x: float) -> float:
            ex = math.exp(x)
            num = ex * (delta**2 - phi**2 - v - ex)
            den = 2.0 * (phi**2 + v + ex) ** 2
            return num / den - (x - a) / tau**2

        big_a = a
        if delta**2 > phi**2 + v:
            big_b = math.log(delta**2 - phi**2 - v)
        else:
            k = 1
            while f(a - k * tau) < 0:
                k += 1
            big_b = a - k * tau

        fa, fb = f(big_a), f(big_b)
        for _ in range(100):
            if abs(big_b - big_a) <= 1e-6:
                break
            big_c = big_a + (big_a - big_b) * fa / (fb - fa)
            fc = f(big_c)
            if fc * fb <= 0:
                big_a, fa = big_b, fb
            else:
                fa /= 2.0
            big_b, fb = big_c, fc
        return math.exp(big_a / 2.0)
