"""Shared ML types and the prediction-method identifiers."""

from __future__ import annotations

import enum


class Method(enum.StrEnum):
    elo = "elo"
    glicko2 = "glicko2"
    dixon_coles = "dixon_coles"
    xg = "xg"
    lightgbm = "lightgbm"
    market = "market"
    consensus = "consensus"


# Methods 1-5 feed the consensus stack; market is the benchmark, consensus the blend.
CONSENSUS_INPUTS: tuple[Method, ...] = (
    Method.elo,
    Method.glicko2,
    Method.dixon_coles,
    Method.xg,
    Method.lightgbm,
)


class Outcome(enum.StrEnum):
    home = "home"
    draw = "draw"
    away = "away"


class DataQuality(enum.StrEnum):
    """Whether a model runs on full-fidelity data or an approximation."""

    FULL = "full"
    APPROXIMATE = "approximate"


# 1X2 probabilities in a fixed order.
Probs1x2 = dict[str, float]
