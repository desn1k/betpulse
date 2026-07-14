"""Numerical unit tests for the ML methods (assertions, not just 'runs')."""

from __future__ import annotations

import numpy as np
import pytest
from app.ml import metrics as M
from app.ml.base import DataQuality
from app.ml.consensus import Consensus
from app.ml.dixon_coles import (
    DixonColes,
    DixonColesParams,
    one_x_two,
    score_matrix,
    total_over_under,
)
from app.ml.elo import Elo
from app.ml.glicko2 import Glicko2, GlickoPlayer, MatchResult
from app.ml.lightgbm_model import LightGbm1x2, time_series_split
from app.ml.market import shin_devig
from app.ml.xg import XgModel

# --- Elo --------------------------------------------------------------------


def test_elo_update_direction() -> None:
    elo = Elo()
    # Stronger home team wins → its rating rises, the loser's falls.
    new_home, new_away = elo.update(1600.0, 1400.0, 2, 0)
    assert new_home > 1600.0
    assert new_away < 1400.0


def test_elo_update_is_zero_sum() -> None:
    elo = Elo()
    h0, a0 = 1500.0, 1500.0
    nh, na = elo.update(h0, a0, 3, 1)
    assert (nh - h0) == pytest.approx(-(na - a0), abs=1e-9)


def test_elo_known_regression() -> None:
    elo = Elo()
    nh, na = elo.update(1500.0, 1500.0, 1, 0)
    # Even ratings, home win by 1 with home advantage → fixed deterministic delta.
    assert nh == pytest.approx(1508.1507, abs=1e-3)
    assert na == pytest.approx(1491.8493, abs=1e-3)


# --- Glicko-2 ---------------------------------------------------------------


def test_glicko_rd_increases_without_matches() -> None:
    g = Glicko2()
    p = GlickoPlayer(rating=1500.0, rd=200.0, volatility=0.06)
    after = g.apply_inactivity(p)
    assert after.rd > p.rd


def test_glicko_rd_decreases_after_match() -> None:
    g = Glicko2()
    p = GlickoPlayer(rating=1500.0, rd=200.0, volatility=0.06)
    after = g.update(p, [MatchResult(1500.0, 200.0, 1.0)])
    assert after.rd < p.rd


# --- Dixon-Coles ------------------------------------------------------------


def test_dixon_coles_matrix_sums_to_one() -> None:
    m = score_matrix(1.6, 1.1, rho=-0.05)
    assert m.sum() == pytest.approx(1.0, abs=1e-6)


def test_dixon_coles_1x2_matches_matrix() -> None:
    m = score_matrix(1.6, 1.1, rho=-0.05)
    probs = one_x_two(m)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)
    # Stronger home expectation → home most likely.
    assert probs["home"] > probs["away"]


def test_dixon_coles_totals_partition() -> None:
    m = score_matrix(1.5, 1.2, rho=-0.05)
    ou = total_over_under(m, 2.5)
    assert ou["over"] + ou["under"] == pytest.approx(1.0, abs=1e-6)


def test_dixon_coles_known_regression() -> None:
    m = score_matrix(1.5, 1.0, rho=-0.05)
    # 0-0 cell is deterministic for fixed parameters.
    assert float(m[0, 0]) == pytest.approx(0.088241, abs=1e-5)


def test_dixon_coles_half_totals() -> None:
    dc = DixonColes(DixonColesParams(attack={"h": 0.2}, defence={"a": -0.1}))
    ou = dc.predict_half_total("h", "a", half="first", line=1.5)
    assert ou["over"] + ou["under"] == pytest.approx(1.0, abs=1e-6)


# --- xG ---------------------------------------------------------------------


def test_xg_shot_probability_in_unit_interval() -> None:
    xg = XgModel(has_coordinates=True)
    for dist, angle in [(1.0, 1.2), (11.0, 0.4), (30.0, 0.1), (50.0, 0.02)]:
        v = xg.shot_xg(dist, angle)
        assert 0.0 < v < 1.0


def test_xg_data_quality_flag() -> None:
    assert XgModel(has_coordinates=False).data_quality is DataQuality.APPROXIMATE
    assert XgModel(has_coordinates=True).data_quality is DataQuality.FULL


def test_xg_regression_to_mean() -> None:
    # A single high observation is pulled toward the league mean.
    est = XgModel.rolling_xg([3.0], league_mean=1.3, regression_strength=6.0)
    assert 1.3 < est < 3.0
    # More observations → closer to the observed mean.
    est_many = XgModel.rolling_xg([3.0] * 10, league_mean=1.3, regression_strength=6.0)
    assert est_many > est


# --- Market (Shin) ----------------------------------------------------------


def test_shin_devig_sums_to_one_and_positive() -> None:
    probs = shin_devig([2.1, 3.4, 3.6])
    assert sum(probs) == pytest.approx(1.0, abs=1e-6)
    assert all(p > 0.0 for p in probs)


def test_shin_removes_margin() -> None:
    odds = [1.5, 4.0, 7.0]
    raw = sum(1.0 / o for o in odds)
    assert raw > 1.0  # bookmaker overround
    probs = shin_devig(odds)
    assert sum(probs) == pytest.approx(1.0, abs=1e-6)


# --- Consensus --------------------------------------------------------------


def _stacked(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    y = rng.integers(0, 3, size=n)
    feats = np.zeros((n, 15))
    for i in range(n):
        base = np.full(3, 0.2)
        base[y[i]] = 0.6
        for m in range(5):
            noisy = np.clip(base + rng.normal(0, 0.05, 3), 0.01, None)
            feats[i, m * 3 : m * 3 + 3] = noisy / noisy.sum()
    return feats, y


def test_consensus_probs_sum_to_one() -> None:
    rng = np.random.default_rng(0)
    feats, y = _stacked(rng, 200)
    c = Consensus()
    c.fit(feats, y)
    out = c.predict_proba(feats[:10])
    assert np.allclose(out.sum(axis=1), 1.0, atol=1e-6)


def test_consensus_isotonic_is_monotonic() -> None:
    rng = np.random.default_rng(1)
    feats, y = _stacked(rng, 200)
    c = Consensus()
    c.fit(feats, y)
    cal = c.calibrator(0)
    xs = np.linspace(0.0, 1.0, 50)
    ys = cal.predict(xs)
    assert np.all(np.diff(ys) >= -1e-9)  # non-decreasing


# --- metrics ----------------------------------------------------------------


def test_brier_and_accuracy_pct() -> None:
    probs = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    y = np.array([0, 1])
    assert M.brier_multiclass(probs, y) == pytest.approx(0.0, abs=1e-9)
    baseline = M.brier_baseline(y)
    assert M.accuracy_pct(0.0, baseline) == pytest.approx(100.0)


def test_shin_devig_rejects_bad_odds() -> None:
    with pytest.raises(ValueError):
        shin_devig([1.0, 2.0])


# --- LightGBM ---------------------------------------------------------------


def test_time_series_split_has_no_leakage() -> None:
    dates = np.array(np.arange("2023-01", "2023-09", dtype="datetime64[M]").tolist() * 3)
    folds = time_series_split(dates, n_splits=3)
    assert folds
    for train_idx, val_idx in folds:
        assert train_idx.size and val_idx.size
        # No training sample's date is >= any validation sample's date.
        assert dates[train_idx].max() < dates[val_idx].min()


def test_lightgbm_predicts_normalized_probabilities() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(60, 4))
    y = rng.integers(0, 3, size=60)
    model = LightGbm1x2(num_boost_round=20)
    model.fit(x, y)
    probs = model.predict_proba(x)
    assert probs.shape == (60, 3)
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-6)
