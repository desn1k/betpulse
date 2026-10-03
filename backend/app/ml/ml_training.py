"""LightGBM and consensus training with strictly temporal evaluation.

Both methods learn from the chronological feature table (each row built only
from matches before its kickoff) and report metrics on a **test window that is
strictly later than everything they were fitted or tuned on**. Only test-window
fixtures receive predictions, so every stored LightGBM/consensus probability is
out-of-sample.

Windows are cut on kickoff instants (:func:`app.ml.splits.temporal_windows`):

* **LightGBM** — train 60 % / validation 20 % (only to pick the number of
  boosting rounds by early stopping) / test 20 %.
* **Consensus** — base inputs are Elo, Glicko-2 and Dixon-Coles (prequential:
  out-of-sample one step ahead by construction) plus LightGBM probabilities
  that are **out-of-fold**: inside the first 60 % a rolling origin trains a
  LightGBM on all earlier blocks and predicts the next block. The stacking
  meta-model is fitted on those out-of-fold rows; the isotonic calibration on
  the next window (60-80 %), which the meta-model never saw; metrics on the last
  window (80-100 %), used for neither. The LightGBM feeding each window is
  trained only on rows before that window.

Too little data yields an explicit reason instead of a model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.ml import metrics as metrics_mod
from app.ml.consensus import Consensus
from app.ml.features import FEATURE_COLUMNS
from app.ml.lightgbm_model import TRAINING_PARAMS, LightGbm1x2
from app.ml.splits import assert_ordered_disjoint, temporal_windows

OUTCOMES = ("home", "draw", "away")
MIN_TOTAL = 200
# Technical floors only: below them a window cannot yield a usable fit or score.
# Becoming champion needs far more (``champion_min_samples`` common matches).
MIN_TRAIN = 60
MIN_VALID = 20
MIN_TEST = 40
MIN_META = 60
MIN_CALIBRATION = 40
LIGHTGBM_CUTS = (0.6, 0.8)
CONSENSUS_CUTS = (0.6, 0.8)
OOF_CUTS = (0.25, 0.5, 0.75)  # four rolling-origin blocks inside the first window
LIGHTGBM_MAX_ROUNDS = 300
BASE_LIGHTGBM_ROUNDS = 100
CONSENSUS_BASES = ("elo", "glicko2", "dixon_coles", "lightgbm")

Probs = dict[str, float]


@dataclass(slots=True)
class MlTrained:
    method: str
    predictions: dict[uuid.UUID, Probs]  # test window only
    metrics: dict[str, float]
    params: dict[str, Any] = field(default_factory=dict)
    model: Any = None

    @property
    def sample_count(self) -> int:
        return len(self.predictions)


@dataclass(slots=True)
class MlSkipped:
    method: str
    reason: str


def _xy(features: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[datetime], list[uuid.UUID]]:
    x = features[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = features["label"].to_numpy(dtype=int)
    kickoffs = list(features["kickoff_at"])
    ids = list(features["fixture_id"])
    return x, y, kickoffs, ids


def _test_metrics(probs: np.ndarray, y: np.ndarray, y_reference: np.ndarray) -> dict[str, float]:
    """Test-window metrics. The skill score's baseline uses the base rates of
    the *training* labels, so not even the baseline peeks at the test labels."""
    brier = metrics_mod.brier_multiclass(probs, y)
    base = metrics_mod.base_rate_probs(y_reference)
    baseline = metrics_mod.brier_multiclass(np.tile(base, (len(y), 1)), y)
    return {
        "test_brier": brier,
        "test_log_loss": metrics_mod.log_loss(probs, y),
        "test_hit_rate": metrics_mod.hit_rate(probs, y),
        "test_skill_pct": metrics_mod.accuracy_pct(brier, baseline),
        "test_samples": float(len(y)),
    }


def _window_params(
    prefix: str, kickoffs: list[datetime], windows: list[np.ndarray]
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, window in zip(("train", "valid", "test"), windows, strict=False):
        params[f"{prefix}{name}_rows"] = len(window)
        if len(window):
            params[f"{prefix}{name}_from"] = kickoffs[int(window[0])].isoformat()
            params[f"{prefix}{name}_to"] = kickoffs[int(window[-1])].isoformat()
    return params


def _as_probs(row: np.ndarray) -> Probs:
    return {o: float(p) for o, p in zip(OUTCOMES, row, strict=True)}


def train_lightgbm(features: pd.DataFrame) -> MlTrained | MlSkipped:
    if len(features) < MIN_TOTAL:
        return MlSkipped("lightgbm", f"insufficient samples ({len(features)} < {MIN_TOTAL})")
    x, y, kickoffs, ids = _xy(features)
    windows = temporal_windows(kickoffs, LIGHTGBM_CUTS)
    assert_ordered_disjoint(kickoffs, windows)
    train, valid, test = windows
    for name, window, floor in (
        ("train", train, MIN_TRAIN),
        ("validation", valid, MIN_VALID),
        ("test", test, MIN_TEST),
    ):
        if len(window) < floor:
            return MlSkipped(
                "lightgbm", f"{name} window too small ({len(window)} < {floor} matches)"
            )

    model = LightGbm1x2(num_boost_round=LIGHTGBM_MAX_ROUNDS, params=TRAINING_PARAMS)
    model.fit(x[train], y[train], valid=(x[valid], y[valid]))
    probs = model.predict_proba(x[test])
    return MlTrained(
        method="lightgbm",
        predictions={ids[int(i)]: _as_probs(p) for i, p in zip(test, probs, strict=True)},
        metrics=_test_metrics(probs, y[test], y[train]),
        params={
            **_window_params("", kickoffs, windows),
            "best_iteration": model.best_iteration,
        },
        model=model,
    )


def _fit_base_lightgbm(x: np.ndarray, y: np.ndarray) -> LightGbm1x2:
    model = LightGbm1x2(num_boost_round=BASE_LIGHTGBM_ROUNDS, params=TRAINING_PARAMS)
    model.fit(x, y)
    return model


def _stack(
    rows: np.ndarray,
    ids: list[uuid.UUID],
    base_preds: dict[str, dict[uuid.UUID, Probs]],
    lightgbm_probs: np.ndarray,
) -> np.ndarray:
    """(len(rows), 12): Elo | Glicko-2 | Dixon-Coles | LightGBM 1X2 probabilities."""
    columns = [
        np.array([[base_preds[m][ids[int(i)]][o] for o in OUTCOMES] for i in rows])
        for m in CONSENSUS_BASES[:-1]
    ]
    return np.hstack([*columns, lightgbm_probs])


def train_consensus(
    features: pd.DataFrame, base_preds: dict[str, dict[uuid.UUID, Probs]]
) -> MlTrained | MlSkipped:
    if len(features) < MIN_TOTAL:
        return MlSkipped("consensus", f"insufficient samples ({len(features)} < {MIN_TOTAL})")
    x, y, kickoffs, ids = _xy(features)
    missing = [m for m in CONSENSUS_BASES[:-1] if not all(i in base_preds.get(m, {}) for i in ids)]
    if missing:
        return MlSkipped("consensus", f"base predictions missing for: {', '.join(missing)}")

    oof_region, calibration, test = temporal_windows(kickoffs, CONSENSUS_CUTS)
    assert_ordered_disjoint(kickoffs, [oof_region, calibration, test])

    # Rolling origin inside the first window: block b is predicted by a
    # LightGBM trained on blocks 0..b-1 only. Block 0 is warm-up.
    region_kickoffs = [kickoffs[int(i)] for i in oof_region]
    blocks = [oof_region[b] for b in temporal_windows(region_kickoffs, OOF_CUTS)]
    assert_ordered_disjoint(kickoffs, blocks)
    meta_rows = np.concatenate(blocks[1:]) if len(blocks) > 1 else np.array([], dtype=int)

    for name, window, floor in (
        ("out-of-fold meta-training", meta_rows, MIN_META),
        ("calibration", calibration, MIN_CALIBRATION),
        ("test", test, MIN_TEST),
    ):
        if len(window) < floor:
            return MlSkipped(
                "consensus", f"{name} window too small ({len(window)} < {floor} matches)"
            )
    if len(set(y[meta_rows].tolist())) < 2:
        return MlSkipped("consensus", "out-of-fold window has a single outcome class")

    oof_probs = []
    for b in range(1, len(blocks)):
        earlier = np.concatenate(blocks[:b])
        oof_probs.append(_fit_base_lightgbm(x[earlier], y[earlier]).predict_proba(x[blocks[b]]))
    meta_x = _stack(meta_rows, ids, base_preds, np.vstack(oof_probs))

    calibration_lgb = _fit_base_lightgbm(x[oof_region], y[oof_region])
    calibration_x = _stack(
        calibration, ids, base_preds, calibration_lgb.predict_proba(x[calibration])
    )
    before_test = np.concatenate([oof_region, calibration])
    test_lgb = _fit_base_lightgbm(x[before_test], y[before_test])
    test_x = _stack(test, ids, base_preds, test_lgb.predict_proba(x[test]))

    model = Consensus()
    model.fit_meta(meta_x, y[meta_rows])
    model.fit_calibration(calibration_x, y[calibration])
    probs = model.predict_proba(test_x)

    params = _window_params("", kickoffs, [meta_rows, calibration, test])
    params = {
        k.replace("train_", "meta_").replace("valid_", "calibration_"): v for k, v in params.items()
    }
    params["bases"] = ",".join(CONSENSUS_BASES)
    return MlTrained(
        method="consensus",
        predictions={ids[int(i)]: _as_probs(p) for i, p in zip(test, probs, strict=True)},
        metrics=_test_metrics(probs, y[test], y[meta_rows]),
        params=params,
        model=model,
    )
