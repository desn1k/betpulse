"""LightGBM + consensus training path, and the champion-selection rules."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from app.ml.evaluation import compute_rolling_metrics, latest_versions
from app.ml.features import FEATURE_COLUMNS
from app.ml.ml_training import MlSkipped, train_consensus, train_lightgbm
from app.ml.registry import MethodMetrics, apply_champion_selection
from app.ml.splits import assert_ordered_disjoint, temporal_windows
from app.ml.training import run_training
from app.models.fixture import Fixture, FixtureStatus
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, Team
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

T0 = datetime(2023, 8, 1, 15, 0, tzinfo=UTC)


async def _seed_season(session: AsyncSession, n_teams: int = 12, rounds: int = 3) -> int:
    """Round-robins between teams of fixed strength, so there is signal to learn."""
    rng = np.random.default_rng(11)
    league = League(code=f"L{uuid.uuid4().hex[:5]}", name="League")
    teams = [
        Team(name=f"T{i}", normalized_name=f"t{i}-{uuid.uuid4().hex[:6]}") for i in range(n_teams)
    ]
    session.add_all([league, *teams])
    await session.flush()
    strength = {t.id: float(rng.uniform(0.6, 2.0)) for t in teams}
    count = 0
    for _ in range(rounds):
        for h in teams:
            for a in teams:
                if h is a:
                    continue
                goals = np.random.default_rng(count)
                session.add(
                    Fixture(
                        league_id=league.id,
                        season="2023-2024",
                        home_team_id=h.id,
                        away_team_id=a.id,
                        kickoff_at=T0 + timedelta(hours=8 * count),
                        status=FixtureStatus.finished,
                        ft_home=int(goals.poisson(strength[h.id] * 1.15)),
                        ft_away=int(goals.poisson(strength[a.id])),
                    )
                )
                count += 1
    await session.flush()
    return count


@pytest.mark.asyncio
async def test_lightgbm_and_consensus_train_on_enough_data(session: AsyncSession) -> None:
    n = await _seed_season(session)
    assert n >= 300

    summary = await run_training(session, version="v-big")

    assert "lightgbm" in summary.trained, summary.skipped
    assert "consensus" in summary.trained, summary.skipped
    for method in ("lightgbm", "consensus"):
        rows = (
            (
                await session.execute(
                    select(Prediction).where(
                        Prediction.method == method, Prediction.model_version == "v-big"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows, method
        by_fixture: dict[uuid.UUID, list[float]] = {}
        for p in rows:
            by_fixture.setdefault(p.fixture_id, []).append(float(p.probability))
        for probs in by_fixture.values():
            assert len(probs) == 3
            assert all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in probs)
            assert sum(probs) == pytest.approx(1.0, abs=1e-4)
        # Out-of-sample only: never a prediction for every fixture.
        assert len(by_fixture) < n


def _m(brier: float, n: int = 400, acc: float = 0.0) -> MethodMetrics:
    return MethodMetrics(
        accuracy_pct=acc, brier=brier, log_loss=1.0, roi_vs_closing=0.0, sample_count=n
    )


async def _registry(session: AsyncSession, champion: str | None, *methods: str) -> None:
    for m in methods:
        session.add(
            ModelRegistry(
                method=m,
                version="v1",
                status=ModelStatus.champion if m == champion else ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("0"),
                min_samples=1,
                sample_count=0,
            )
        )
    await session.flush()


@pytest.mark.asyncio
async def test_primary_metric_is_brier_not_accuracy_pct(session: AsyncSession) -> None:
    await _registry(session, None, "elo", "dixon_coles")
    # elo has the higher skill % (against a different baseline) but the worse
    # Brier on the same fixtures: Brier decides.
    best = await apply_champion_selection(
        session,
        {"elo": _m(0.21, acc=40.0), "dixon_coles": _m(0.19, acc=10.0)},
        min_samples=100,
    )
    assert best == "dixon_coles"


@pytest.mark.asyncio
async def test_candidate_must_beat_champion_by_a_margin(session: AsyncSession) -> None:
    await _registry(session, "elo", "elo", "dixon_coles")
    best = await apply_champion_selection(
        session,
        {"elo": _m(0.2000, acc=10.0), "dixon_coles": _m(0.1999, acc=10.05)},
        min_samples=100,
    )
    assert best == "elo"  # 0.0001 better is noise, not an improvement


@pytest.mark.asyncio
async def test_candidate_below_min_samples_never_replaces_champion(session: AsyncSession) -> None:
    await _registry(session, "elo", "elo", "dixon_coles")
    best = await apply_champion_selection(
        session,
        {"elo": _m(0.22, acc=5.0), "dixon_coles": _m(0.10, n=50, acc=50.0)},
        min_samples=100,
    )
    assert best == "elo"


@pytest.mark.asyncio
async def test_failed_candidate_never_replaces_champion(session: AsyncSession) -> None:
    await _registry(session, "elo", "elo", "dixon_coles")
    best = await apply_champion_selection(
        session,
        {"elo": _m(0.22, acc=5.0), "dixon_coles": _m(float("nan"), acc=float("nan"))},
        min_samples=100,
    )
    assert best == "elo"


@pytest.mark.asyncio
async def test_methods_are_compared_on_identical_fixtures(session: AsyncSession) -> None:
    """A method that only predicted the easy fixtures must be scored on the
    fixtures every compared method predicted, not on its own easy subset."""
    league = League(code=f"L{uuid.uuid4().hex[:5]}", name="League")
    teams = [Team(name=f"T{i}", normalized_name=f"t{i}-{uuid.uuid4().hex[:6]}") for i in range(2)]
    session.add_all([league, *teams])
    await session.flush()
    fixtures = []
    for i in range(200):
        home_win = i < 100  # the first 100 are "easy" home wins
        fixtures.append(
            Fixture(
                league_id=league.id,
                season="2024-2025",
                home_team_id=teams[0].id,
                away_team_id=teams[1].id,
                kickoff_at=T0 + timedelta(hours=i),
                status=FixtureStatus.finished,
                ft_home=2 if home_win else 0,
                ft_away=0 if home_win else 2,
            )
        )
    session.add_all(fixtures)
    await session.flush()

    def add(method: str, fx: Fixture, probs: tuple[float, float, float]) -> None:
        for outcome, p in zip(("home", "draw", "away"), probs, strict=True):
            session.add(
                Prediction(
                    fixture_id=fx.id,
                    method=method,
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal(str(p)),
                    model_version="v1",
                )
            )

    for i, fx in enumerate(fixtures):
        add("elo", fx, (0.6, 0.2, 0.2) if i < 100 else (0.2, 0.2, 0.6))
        if i < 100:
            add("lucky", fx, (0.95, 0.03, 0.02))
    await _registry(session, "elo", "elo", "lucky")
    await session.flush()

    metrics = await compute_rolling_metrics(session, window_days=400, now=T0 + timedelta(days=30))
    assert metrics["elo"].sample_count == metrics["lucky"].sample_count == 100


# --- temporal windows -------------------------------------------------------


def _instants(n: int, per: int = 1) -> list[datetime]:
    return [T0 + timedelta(days=i // per) for i in range(n)]


def test_windows_are_disjoint_ordered_and_cover_everything() -> None:
    kickoffs = _instants(100)
    windows = temporal_windows(kickoffs, (0.6, 0.8))
    assert [len(w) for w in windows] == [60, 20, 20]
    assert sorted(np.concatenate(windows).tolist()) == list(range(100))
    assert_ordered_disjoint(kickoffs, windows)
    for earlier, later in zip(windows, windows[1:], strict=False):
        assert max(kickoffs[i] for i in earlier) < min(kickoffs[i] for i in later)


def test_windows_never_split_a_same_kickoff_batch() -> None:
    kickoffs = _instants(99, per=3)  # 33 instants, three fixtures each
    window_of: dict[datetime, set[int]] = {}
    for k, window in enumerate(temporal_windows(kickoffs, (0.6, 0.8))):
        for i in window:
            window_of.setdefault(kickoffs[i], set()).add(k)
    assert all(len(ks) == 1 for ks in window_of.values())


def test_windows_reject_unordered_input() -> None:
    with pytest.raises(ValueError, match="chronological"):
        temporal_windows([T0 + timedelta(days=1), T0], (0.5,))
    with pytest.raises(AssertionError):
        assert_ordered_disjoint(_instants(4), [np.array([0, 2]), np.array([1, 3])])


# --- explicit skip reasons --------------------------------------------------


def _frame(n: int) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    data: dict[str, object] = {c: rng.normal(size=n) for c in FEATURE_COLUMNS}
    data["label"] = rng.integers(0, 3, size=n)
    data["kickoff_at"] = _instants(n)
    data["fixture_id"] = [uuid.UUID(int=i + 1) for i in range(n)]
    return pd.DataFrame(data)


def test_too_few_samples_give_an_explicit_reason() -> None:
    small = _frame(50)
    lgb = train_lightgbm(small)
    cons = train_consensus(small, {})
    assert isinstance(lgb, MlSkipped) and "insufficient samples (50 < 200)" in lgb.reason
    assert isinstance(cons, MlSkipped) and "insufficient samples (50 < 200)" in cons.reason


def test_missing_base_predictions_give_an_explicit_reason() -> None:
    result = train_consensus(_frame(300), {"elo": {}})
    assert isinstance(result, MlSkipped)
    assert "base predictions missing" in result.reason


def test_too_small_test_window_gives_an_explicit_reason() -> None:
    frame = _frame(220)
    # 210 of the rows share one kickoff instant: windows are cut on instants,
    # so the earlier windows hold only the 10 distinct early matches.
    frame.loc[10:, "kickoff_at"] = T0 + timedelta(days=500)
    result = train_lightgbm(frame)
    assert isinstance(result, MlSkipped)
    assert "window too small" in result.reason


# --- out-of-sample only, and reruns stay separate ---------------------------


async def _kickoffs_by_fixture(session: AsyncSession) -> dict[uuid.UUID, datetime]:
    rows = (await session.execute(select(Fixture.id, Fixture.kickoff_at))).all()
    return {fid: kickoff for fid, kickoff in rows}


@pytest.mark.asyncio
async def test_ml_predictions_cover_only_the_latest_window(session: AsyncSession) -> None:
    await _seed_season(session)
    summary = await run_training(session, version="v-oos")
    kickoff = await _kickoffs_by_fixture(session)

    for method in ("lightgbm", "consensus"):
        predicted = set(
            (
                await session.execute(
                    select(Prediction.fixture_id).where(
                        Prediction.method == method, Prediction.model_version == "v-oos"
                    )
                )
            ).scalars()
        )
        unpredicted = set(kickoff) - predicted
        # The test window is strictly later than every fixture used to fit,
        # tune or calibrate the model.
        assert min(kickoff[f] for f in predicted) > max(kickoff[f] for f in unpredicted)
        assert summary.metrics[method]["test_samples"] == len(predicted)
        assert all(math.isfinite(v) for v in summary.metrics[method].values())


@pytest.mark.asyncio
async def test_every_stored_probability_is_finite_and_normalised(session: AsyncSession) -> None:
    await _seed_season(session)
    await run_training(session, version="v-probs")
    rows = (await session.execute(select(Prediction))).scalars().all()
    by_key: dict[tuple[uuid.UUID, str], list[float]] = {}
    for p in rows:
        by_key.setdefault((p.fixture_id, p.method), []).append(float(p.probability))
    assert {m for _, m in by_key} >= {"elo", "glicko2", "dixon_coles", "lightgbm", "consensus"}
    for probs in by_key.values():
        assert len(probs) == 3
        assert all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in probs)
        assert sum(probs) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.asyncio
async def test_retraining_creates_a_new_version_without_mixing_runs(session: AsyncSession) -> None:
    await _seed_season(session)
    await run_training(session, version="v1")
    await run_training(session, version="v2")

    versions_by_method: dict[str, set[str]] = {}
    for row in (await session.execute(select(ModelRegistry))).scalars():
        versions_by_method.setdefault(row.method, set()).add(row.version)
    for method in ("elo", "glicko2", "dixon_coles", "lightgbm", "consensus"):
        assert versions_by_method[method] == {"v1", "v2"}, method
    assert set((await latest_versions(session)).values()) == {"v2"}

    # Each run wrote its own rows; the same data gives the same row count.
    per_version = dict(
        (
            await session.execute(
                select(Prediction.model_version, func.count()).group_by(Prediction.model_version)
            )
        )
        .tuples()
        .all()
    )
    assert per_version["v1"] == per_version["v2"] > 0

    metrics = await compute_rolling_metrics(session, window_days=400, now=T0 + timedelta(days=200))
    assert metrics
    assert {m.version for m in metrics.values()} == {"v2"}


# --- remaining champion rules -----------------------------------------------


@pytest.mark.asyncio
async def test_candidate_beyond_the_margin_replaces_champion(session: AsyncSession) -> None:
    await _registry(session, "elo", "elo", "dixon_coles")
    best = await apply_champion_selection(
        session, {"elo": _m(0.2000), "dixon_coles": _m(0.1970)}, min_samples=100
    )
    assert best == "dixon_coles"
    rows = (await session.execute(select(ModelRegistry))).scalars().all()
    assert {r.method: r.status for r in rows} == {
        "elo": ModelStatus.challenger,
        "dixon_coles": ModelStatus.champion,
    }


@pytest.mark.asyncio
async def test_without_a_champion_the_best_eligible_wins_without_margin(
    session: AsyncSession,
) -> None:
    await _registry(session, None, "elo", "dixon_coles")
    best = await apply_champion_selection(
        session, {"elo": _m(0.2000), "dixon_coles": _m(0.1999)}, min_samples=100
    )
    assert best == "dixon_coles"


@pytest.mark.asyncio
async def test_an_ineligible_champion_is_replaced(session: AsyncSession) -> None:
    await _registry(session, "elo", "elo", "dixon_coles")
    best = await apply_champion_selection(
        session, {"elo": _m(0.15, n=50), "dixon_coles": _m(0.21)}, min_samples=100
    )
    assert best == "dixon_coles"


@pytest.mark.asyncio
async def test_failed_metrics_are_not_stored_as_numbers(session: AsyncSession) -> None:
    await _registry(session, None, "elo")
    await apply_champion_selection(session, {"elo": _m(float("nan"))}, min_samples=1)
    row = await session.scalar(select(ModelRegistry).where(ModelRegistry.method == "elo"))
    assert row is not None
    assert row.brier is None
    assert row.status == ModelStatus.challenger


@pytest.mark.asyncio
async def test_margin_protects_a_champion_whose_newer_version_was_evaluated(
    session: AsyncSession,
) -> None:
    """After a retrain the champion row is elo v1 while the evaluation scored
    elo v2; a challenger better by less than the margin must not take over."""
    now = datetime.now(UTC)
    session.add_all(
        [
            ModelRegistry(
                method="elo",
                version="v1",
                status=ModelStatus.champion,
                is_enabled=True,
                last_trained_at=now - timedelta(days=1),
            ),
            ModelRegistry(
                method="elo",
                version="v2",
                status=ModelStatus.challenger,
                is_enabled=True,
                last_trained_at=now,
            ),
            ModelRegistry(
                method="dixon_coles",
                version="v2",
                status=ModelStatus.challenger,
                is_enabled=True,
                last_trained_at=now,
            ),
        ]
    )
    await session.flush()
    elo_v2 = _m(0.2000)
    elo_v2.version = "v2"
    dc = _m(0.1990)
    dc.version = "v2"

    best = await apply_champion_selection(
        session, {"elo": elo_v2, "dixon_coles": dc}, min_samples=100
    )

    assert best == "elo"
    status = {
        (r.method, r.version): r.status
        for r in (await session.execute(select(ModelRegistry))).scalars()
    }
    # The method keeps the title; its evaluated version now carries it.
    assert status == {
        ("elo", "v1"): ModelStatus.challenger,
        ("elo", "v2"): ModelStatus.champion,
        ("dixon_coles", "v2"): ModelStatus.challenger,
    }
