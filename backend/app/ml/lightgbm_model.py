"""LightGBM models.

Multiclass 1X2 + a separate goals regression. Cross-validation is
**time-series-safe**: folds are split by date so no training sample is dated at
or after any validation sample (``time_series_split`` guarantees and the tests
assert this). Features are assembled in ``app/ml/features.py``.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np

_CLASS_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 1,
    "min_data_in_bin": 1,
    "verbose": -1,
    "seed": 42,
}

_REG_PARAMS = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 1,
    "min_data_in_bin": 1,
    "verbose": -1,
    "seed": 42,
}


def time_series_split(dates: np.ndarray, n_splits: int = 3) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window CV split by date.

    For each fold, every training row's date is strictly earlier than every
    validation row's date (no leakage). Boundaries fall on distinct dates.
    """
    dates = np.asarray(dates)
    unique = np.unique(dates)
    if len(unique) < n_splits + 1:
        raise ValueError("not enough distinct dates for the requested splits")

    # n_splits+1 boundary dates carve n_splits validation blocks.
    positions = [int(len(unique) * (f + 1) / (n_splits + 1)) for f in range(n_splits + 1)]
    boundaries = [unique[min(p, len(unique) - 1)] for p in positions]

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for f in range(n_splits):
        lo = boundaries[f]
        hi = boundaries[f + 1]
        train_idx = np.where(dates <= lo)[0]
        val_idx = np.where((dates > lo) & (dates <= hi))[0]
        if len(train_idx) and len(val_idx):
            folds.append((train_idx, val_idx))
    return folds


class LightGbm1x2:
    def __init__(self, num_boost_round: int = 60) -> None:
        self._rounds = num_boost_round
        self._booster: lgb.Booster | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> None:
        dataset = lgb.Dataset(x, label=y)
        self._booster = lgb.train(_CLASS_PARAMS, dataset, num_boost_round=self._rounds)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if self._booster is None:
            raise RuntimeError("model is not fitted")
        probs = np.asarray(self._booster.predict(x)).reshape(len(x), 3)
        # LightGBM multiclass already softmax-normalizes; guard anyway.
        normalized: np.ndarray = probs / probs.sum(axis=1, keepdims=True)
        return normalized


class LightGbmGoals:
    def __init__(self, num_boost_round: int = 60) -> None:
        self._rounds = num_boost_round
        self._booster: lgb.Booster | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> None:
        dataset = lgb.Dataset(x, label=y)
        self._booster = lgb.train(_REG_PARAMS, dataset, num_boost_round=self._rounds)

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self._booster is None:
            raise RuntimeError("model is not fitted")
        preds: np.ndarray = np.asarray(self._booster.predict(x))
        return preds
