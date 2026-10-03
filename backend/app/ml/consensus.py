"""Consensus: stacking meta-model + isotonic calibration.

A logistic-regression meta-model stacks the 1X2 probabilities of the base
methods, then a per-class isotonic calibration maps the stacked probabilities
onto empirically calibrated ones (monotonic by construction). Output
probabilities are renormalized to sum to 1.

The honest training path fits the two stages on **different, consecutive**
windows: :meth:`Consensus.fit_meta` on out-of-fold base probabilities, then
:meth:`Consensus.fit_calibration` on a strictly later window the meta-model
never saw. :meth:`Consensus.fit` (both stages on the same rows) remains for
unit tests and exploratory use only — its calibration is in-sample.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

_N_CLASSES = 3
_PROB_FLOOR = 0.01


class Consensus:
    def __init__(self) -> None:
        self._meta = LogisticRegression(max_iter=1000)
        self._calibrators: list[IsotonicRegression] = []

    def fit(self, method_probs: np.ndarray, y: np.ndarray) -> None:
        """Both stages on the same rows (in-sample calibration — tests only).
        ``method_probs``: (n, k*3) stacked 1X2 probs; ``y``: labels 0/1/2."""
        self.fit_meta(method_probs, y)
        self.fit_calibration(method_probs, y)

    def fit_meta(self, method_probs: np.ndarray, y: np.ndarray) -> None:
        """Fit the stacking meta-model (on out-of-fold base probabilities)."""
        self._meta.fit(method_probs, y)

    def fit_calibration(self, method_probs: np.ndarray, y: np.ndarray) -> None:
        """Fit the per-class isotonic maps on a window the meta-model never saw."""
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
        # Isotonic steps on a small calibration window readily hit 0 for an
        # outcome (or all three on an unseen region). A 1 % floor keeps every
        # probability usable — no near-zero mass that a single miss turns into
        # an unbounded log loss — and the row still sums to 1.
        calibrated = np.clip(calibrated, _PROB_FLOOR, None)
        normalized: np.ndarray = calibrated / calibrated.sum(axis=1, keepdims=True)
        return normalized

    def calibrator(self, outcome_index: int) -> IsotonicRegression:
        return self._calibrators[outcome_index]
