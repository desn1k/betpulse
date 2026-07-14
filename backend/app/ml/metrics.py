"""Out-of-sample evaluation metrics.

All operate on multiclass 1X2 probabilities (order: home, draw, away) and integer
labels (0/1/2). ``accuracy_pct`` is the normalized skill score used by the model
registry so methods are comparable regardless of scale.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-15


def _one_hot(y: np.ndarray, n_classes: int) -> np.ndarray:
    oh = np.zeros((len(y), n_classes))
    oh[np.arange(len(y)), y] = 1.0
    return oh


def brier_multiclass(probs: np.ndarray, y: np.ndarray) -> float:
    oh = _one_hot(y, probs.shape[1])
    return float(np.mean(np.sum((probs - oh) ** 2, axis=1)))


def log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    clipped = np.clip(probs, _EPS, 1.0)
    picked = clipped[np.arange(len(y)), y]
    return float(-np.mean(np.log(picked)))


def hit_rate(probs: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.argmax(probs, axis=1) == y))


def base_rate_probs(y: np.ndarray, n_classes: int = 3) -> np.ndarray:
    counts = np.bincount(y, minlength=n_classes).astype(float)
    probs: np.ndarray = counts / counts.sum()
    return probs


def brier_baseline(y: np.ndarray, n_classes: int = 3) -> float:
    """Brier score of always predicting the (in-sample) base rates."""
    base = base_rate_probs(y, n_classes)
    probs = np.tile(base, (len(y), 1))
    return brier_multiclass(probs, y)


def accuracy_pct(brier: float, baseline: float) -> float:
    """Normalized skill score in %: 100 * (1 - brier / brier_baseline)."""
    if baseline <= 0:
        return 0.0
    return 100.0 * (1.0 - brier / baseline)


def roi_vs_closing(
    probs: np.ndarray, y: np.ndarray, closing_odds: np.ndarray, edge: float = 0.0
) -> float:
    """Flat-stake ROI of betting each outcome whose model edge (p*odds - 1) > edge,
    settled at the closing odds. Returns profit per unit staked."""
    staked = 0.0
    profit = 0.0
    for i in range(len(y)):
        for k in range(probs.shape[1]):
            if probs[i, k] * closing_odds[i, k] - 1.0 > edge:
                staked += 1.0
                profit += (closing_odds[i, k] - 1.0) if y[i] == k else -1.0
    if staked == 0.0:
        return 0.0
    return profit / staked
