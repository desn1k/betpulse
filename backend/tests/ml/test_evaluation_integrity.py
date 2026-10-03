"""Evaluation integrity of the ML pipeline (no leakage, explicit time rules).

Each property here is what makes a reported metric honest:

* a fixture is predicted from strictly earlier matches only (its own result,
  and results of matches kicking off at the same moment, never feed into it);
* the processing order is deterministic;
* the odds used are chosen by an explicit time rule, not by row order;
* an evaluation scores one explicit model version.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from app.ml import metrics as metrics_mod
from app.ml import training
from app.ml.evaluation import compute_rolling_metrics
from app.ml.features import build_feature_table
from app.ml.market import shin_devig
from app.models.backtester import BacktestFeature
from app.models.fixture import Fixture, FixtureStatus
from app.models.market import Odds
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.services.backtester.population import populate_backtest_features
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T0 = datetime(2024, 8, 1, 15, 0, tzinfo=UTC)
TEAMS = [uuid.UUID(int=i) for i in range(1, 7)]
RUNNERS = {
    "elo": training._run_elo,
    "glicko2": training._run_glicko,
    "dixon_coles": training._run_dixon_coles,
}


def _fx(home: int, away: int, day: int, score: tuple[int, int], hour: int = 0) -> Fixture:
    return Fixture(
        id=uuid.uuid4(),
        home_team_id=TEAMS[home],
        away_team_id=TEAMS[away],
        kickoff_at=T0 + timedelta(days=day, hours=hour),
        ft_home=score[0],
        ft_away=score[1],
    )


def _history() -> list[Fixture]:
    """A small round-robin so every team has earlier matches."""
    rng = np.random.default_rng(7)
    out = []
    day = 0
    for _ in range(3):
        for h in range(4):
            for a in range(4):
                if h != a:
                    out.append(_fx(h, a, day, (int(rng.integers(0, 4)), int(rng.integers(0, 4)))))
                    day += 1
    return out


def _close(a: dict[str, float], b: dict[str, float]) -> bool:
    return all(abs(a[k] - b[k]) < 1e-12 for k in ("home", "draw", "away"))


# --- 1. a fixture's own result never feeds its own prediction ---------------


@pytest.mark.parametrize("method", sorted(RUNNERS))
def test_own_score_does_not_change_own_prediction(method: str) -> None:
    run = RUNNERS[method]
    fixtures = _history()
    target = fixtures[len(fixtures) // 2]
    before = run(fixtures)[target.id]

    target.ft_home, target.ft_away = 9, 0  # a wildly different result
    after = run(fixtures)[target.id]

    assert _close(before, after), (before, after)


@pytest.mark.parametrize("method", sorted(RUNNERS))
def test_later_results_do_not_change_earlier_predictions(method: str) -> None:
    run = RUNNERS[method]
    fixtures = _history()
    cut = len(fixtures) // 2
    before = run(fixtures)
    for fx in fixtures[cut:]:
        fx.ft_home, fx.ft_away = 0, 7
    after = run(fixtures)
    for fx in fixtures[:cut]:
        assert _close(before[fx.id], after[fx.id]), fx.kickoff_at


# --- 2. same-kickoff matches are one batch; order is deterministic --------


def _same_kickoff_pair() -> tuple[list[Fixture], Fixture, Fixture]:
    """Two matches at the same instant sharing team 0, after some history."""
    fixtures = _history()
    last_day = max(fx.kickoff_at for fx in fixtures)
    day = (last_day - T0).days + 1
    first = _fx(0, 4, day, (3, 0))
    second = _fx(5, 0, day, (1, 1))
    return fixtures, first, second


@pytest.mark.parametrize("method", sorted(RUNNERS))
def test_same_kickoff_matches_do_not_influence_each_other(method: str) -> None:
    run = RUNNERS[method]
    fixtures, first, second = _same_kickoff_pair()
    before = run([*fixtures, first, second])[second.id]
    first.ft_home, first.ft_away = 0, 6
    after = run([*fixtures, first, second])[second.id]
    assert _close(before, after), (before, after)


@pytest.mark.parametrize("method", sorted(RUNNERS))
def test_same_kickoff_order_is_irrelevant(method: str) -> None:
    run = RUNNERS[method]
    fixtures, first, second = _same_kickoff_pair()
    a = run([*fixtures, first, second])
    b = run([*fixtures, second, first])
    for fid in a:
        assert _close(a[fid], b[fid])


async def _seed_league(session: AsyncSession) -> tuple[League, list[Team]]:
    league = League(code=f"L{uuid.uuid4().hex[:5]}", name="League")
    teams = [Team(name=f"T{i}", normalized_name=f"t{i}-{uuid.uuid4().hex[:6]}") for i in range(6)]
    session.add_all([league, *teams])
    await session.flush()
    return league, teams


def _db_fixture(
    league: League, home: Team, away: Team, kickoff: datetime, score: tuple[int, int]
) -> Fixture:
    return Fixture(
        league_id=league.id,
        season="2024-2025",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=kickoff,
        status=FixtureStatus.finished,
        ft_home=score[0],
        ft_away=score[1],
    )


@pytest.mark.asyncio
async def test_feature_rows_for_same_kickoff_matches_are_independent(
    session: AsyncSession,
) -> None:
    league, t = await _seed_league(session)
    session.add_all(
        [
            _db_fixture(league, t[0], t[1], T0, (2, 1)),
            _db_fixture(league, t[2], t[3], T0 + timedelta(days=1), (0, 0)),
        ]
    )
    same = T0 + timedelta(days=7)
    first = _db_fixture(league, t[0], t[4], same, (4, 0))
    second = _db_fixture(league, t[5], t[0], same, (1, 1))
    session.add_all([first, second])
    await session.flush()

    before = (await build_feature_table(session)).set_index("fixture_id").loc[second.id]
    first.ft_home, first.ft_away = 0, 5
    await session.flush()
    after = (await build_feature_table(session)).set_index("fixture_id").loc[second.id]

    for column in ("elo_away", "glicko_away", "form_away", "rest_days_away"):
        assert before[column] == after[column], column


# --- 4. odds chosen by an explicit time rule -------------------------------

# (offset from kickoff, home price): only the quote at/just before kickoff is
# the closing line; the early one is stale and the late one is in-play.
_QUOTES = [(-timedelta(days=2), "1.05"), (timedelta(0), "2.00"), (timedelta(hours=1), "1.01")]


async def _seed_quotes(session: AsyncSession, order: list[int]) -> Fixture:
    league, t = await _seed_league(session)
    fx = _db_fixture(league, t[0], t[1], T0, (1, 0))
    session.add(fx)
    await session.flush()
    for idx in order:
        offset, home = _QUOTES[idx]
        for outcome, price in (("home", home), ("draw", "3.40"), ("away", "4.00")):
            session.add(
                Odds(
                    fixture_id=fx.id,
                    bookmaker="pinnacle",
                    market="1x2",
                    outcome=outcome,
                    ts=fx.kickoff_at + offset,
                    price=Decimal(price),
                    is_closing=True,
                )
            )
        await session.flush()
    return fx


_ORDERS = [[0, 1, 2], [2, 1, 0], [1, 2, 0], [2, 0, 1]]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", _ORDERS)
async def test_market_model_uses_the_closing_quote(session: AsyncSession, order: list[int]) -> None:
    fx = await _seed_quotes(session, order)
    preds = training._run_market(await training._odds_map(session, [fx]))
    expected = dict(zip(("home", "draw", "away"), shin_devig([2.0, 3.4, 4.0]), strict=True))
    assert _close(preds[fx.id], expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", _ORDERS)
async def test_backtest_features_use_the_closing_quote(
    session: AsyncSession, order: list[int]
) -> None:
    fx = await _seed_quotes(session, order)
    await populate_backtest_features(session)
    row = await session.scalar(select(BacktestFeature).where(BacktestFeature.fixture_id == fx.id))
    assert row is not None
    assert row.odds_home == Decimal("2.000")


@pytest.mark.asyncio
@pytest.mark.parametrize("order", _ORDERS)
async def test_evaluation_roi_settles_at_the_closing_quote(
    session: AsyncSession, order: list[int]
) -> None:
    fx = await _seed_quotes(session, order)
    session.add(
        ModelRegistry(method="elo", version="v1", status=ModelStatus.challenger, sample_count=1)
    )
    for outcome, p in (("home", "0.9"), ("draw", "0.05"), ("away", "0.05")):
        session.add(
            Prediction(
                fixture_id=fx.id,
                method="elo",
                market="1x2",
                outcome=outcome,
                probability=Decimal(p),
                model_version="v1",
            )
        )
    await session.flush()

    metrics = await compute_rolling_metrics(
        session, window_days=30, now=fx.kickoff_at + timedelta(days=1)
    )
    # p(home)=0.9 at the closing 2.00 is the only value bet; home won → +1.0/unit.
    assert metrics["elo"].roi_vs_closing == pytest.approx(1.0)


# --- 5. one explicit model version per evaluation --------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("insert_newer_first", [True, False])
async def test_evaluation_scores_only_the_current_version(
    session: AsyncSession, insert_newer_first: bool
) -> None:
    league, t = await _seed_league(session)
    fixtures = [
        _db_fixture(league, t[i % 4], t[(i + 1) % 4], T0 + timedelta(days=i), score)
        for i, score in enumerate([(2, 0), (0, 0), (0, 1), (3, 1), (1, 1), (0, 2)])
    ]
    session.add_all(fixtures)
    await session.flush()

    old = {"home": 0.34, "draw": 0.33, "away": 0.33}
    new = {"home": 0.6, "draw": 0.25, "away": 0.15}
    versions = [("v-new", new), ("v-old", old)]
    if not insert_newer_first:
        versions.reverse()
    for version, probs in versions:
        for fx in fixtures:
            for outcome, p in probs.items():
                session.add(
                    Prediction(
                        fixture_id=fx.id,
                        method="elo",
                        market="1x2",
                        outcome=outcome,
                        probability=Decimal(str(p)),
                        model_version=version,
                    )
                )
        await session.flush()
    trained = datetime.now(UTC)
    session.add_all(
        [
            ModelRegistry(
                method="elo",
                version="v-old",
                status=ModelStatus.challenger,
                last_trained_at=trained - timedelta(days=1),
            ),
            ModelRegistry(
                method="elo",
                version="v-new",
                status=ModelStatus.challenger,
                last_trained_at=trained,
            ),
        ]
    )
    await session.flush()

    metrics = await compute_rolling_metrics(session, window_days=60, now=T0 + timedelta(days=30))

    y = np.array([0, 1, 2, 0, 1, 2])
    expected_probs = np.tile([new["home"], new["draw"], new["away"]], (len(y), 1))
    assert metrics["elo"].sample_count == len(y)
    assert metrics["elo"].brier == pytest.approx(metrics_mod.brier_multiclass(expected_probs, y))


# --- 3. the backtester's season split is not presented as out-of-sample ---


def test_backtest_result_states_its_evaluation_protocol() -> None:
    from app.schemas.backtester import BacktestResult

    fields = BacktestResult.model_fields
    assert "evaluation_protocol" in fields
    # Grouping already-settled bets by season retrains nothing: no field may
    # claim an out-of-sample / walk-forward result.
    for name in fields:
        assert "out_of_sample" not in name and "walk_forward" not in name, name


@pytest.mark.asyncio
async def test_new_version_of_the_champion_method_never_yields_two_champions(
    session: AsyncSession,
) -> None:
    from app.ml.registry import MethodMetrics, apply_champion_selection

    now = datetime.now(UTC)
    session.add_all(
        [
            ModelRegistry(
                method="elo",
                version="v1",
                status=ModelStatus.champion,
                last_trained_at=now - timedelta(days=1),
            ),
            ModelRegistry(
                method="elo", version="v2", status=ModelStatus.challenger, last_trained_at=now
            ),
        ]
    )
    await session.flush()

    metrics = {
        "elo": MethodMetrics(
            accuracy_pct=10.0,
            brier=0.2,
            log_loss=1.0,
            roi_vs_closing=0.0,
            sample_count=500,
            version="v2",
        )
    }
    assert await apply_champion_selection(session, metrics, min_samples=1) == "elo"

    rows = {r.version: r for r in (await session.execute(select(ModelRegistry))).scalars()}
    assert rows["v2"].status == ModelStatus.champion
    assert rows["v1"].status == ModelStatus.challenger
    assert rows["v2"].brier is not None and rows["v1"].brier is None


@pytest.mark.asyncio
async def test_served_probabilities_never_mix_versions(session: AsyncSession) -> None:
    from app.api.matches import _latest_1x2

    league, t = await _seed_league(session)
    fx = _db_fixture(league, t[0], t[1], T0, (1, 0))
    session.add(fx)
    await session.flush()
    for outcome, p in (("home", "0.5"), ("draw", "0.3"), ("away", "0.2")):
        session.add(
            Prediction(
                fixture_id=fx.id,
                method="elo",
                market="1x2",
                outcome=outcome,
                probability=Decimal(p),
                model_version="v1",
                created_at=T0,
            )
        )
    # A newer version that (so far) only wrote one outcome.
    session.add(
        Prediction(
            fixture_id=fx.id,
            method="elo",
            market="1x2",
            outcome="home",
            probability=Decimal("0.9"),
            model_version="v2",
            created_at=T0 + timedelta(hours=1),
        )
    )
    await session.flush()

    served = (await _latest_1x2(session, [fx.id]))[0][fx.id]["elo"]
    # Everything from v2 only: no v1 draw/away glued onto the v2 home price.
    assert served == {"home": 0.9}
