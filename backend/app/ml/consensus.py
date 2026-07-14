"""Consensus: stacking meta-model + isotonic calibration.

A logistic-regression meta-model stacks the 1X2 probabilities of methods 1-5,
then a per-class isotonic calibration maps the stacked probabilities onto
empirically calibrated ones (monotonic by construction). Output probabilities are
renormalized to sum to 1.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

_N_CLASSES = 3


class Consensus:
    def __init__(self) -> None:
        self._meta = LogisticRegression(max_iter=1000)
        self._calibrators: list[IsotonicRegression] = []

    def fit(self, method_probs: np.ndarray, y: np.ndarray) -> None:
        """``method_probs``: (n, 5*3) stacked 1X2 probs; ``y``: labels 0/1/2."""
        self._meta.fit(method_probs, y)
        raw = self._meta.predict_proba(method_probs)
        self._calibrators = []
        classes = list(self._meta.classes_)
        for k in range(_N_CLASSES):
            cal = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
            if k in classes:
                col = classes.index(k)
                cal.fit(raw[:, col], (y == k).astype(float))
            else:  # pragma: no cover - all three outcomes present in practice
                cal.fit(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
            self._calibrators.append(cal)

    def predict_proba(self, method_probs: np.ndarray) -> np.ndarray:
        raw = self._meta.predict_proba(method_probs)
        classes = list(self._meta.classes_)
        calibrated = np.zeros((len(method_probs), _N_CLASSES))
        for k in range(_N_CLASSES):
            col = classes.index(k) if k in classes else None
            source = raw[:, col] if col is not None else np.zeros(len(method_probs))
            calibrated[:, k] = self._calibrators[k].predict(source)
        totals = calibrated.sum(axis=1, keepdims=True)
        totals[totals == 0.0] = 1.0
        return calibrated / totals

    def calibrator(self, outcome_index: int) -> IsotonicRegression:
        return self._calibrators[outcome_index]
