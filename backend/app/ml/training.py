"""Training pipeline.

Full path per method: build features → train → log to MLflow (binary +
feature_schema.json + training_data_hash + metrics) → write predictions →
upsert model_registry (+ a model_runs row).

Elo, Glicko-2, Dixon-Coles and the market benchmark run on any amount of data;
their predictions are prequential (each from matches strictly before kickoff).
LightGBM and the consensus stack (:mod:`app.ml.ml_training`) train on temporal
windows and predict only their later test window, so their stored predictions
are out-of-sample. With too little data they are **skipped with an explicit
reason** in ``TrainingSummary.skipped`` — never silently.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ml import metrics as metrics_mod
from app.ml.base import Method
from app.ml.chronology import chronological_batches
from app.ml.dixon_coles import DixonColes, DixonColesParams
from app.ml.elo import DEFAULT_RATING, Elo, EloConfig
from app.ml.features import build_feature_table, feature_schema, finished_scores
from app.ml.glicko2 import Glicko2, GlickoPlayer, MatchResult
from app.ml.market import shin_devig
from app.ml.ml_training import MlSkipped, train_consensus, train_lightgbm
from app.ml.mlflow_utils import log_training_run, training_data_hash
from app.ml.odds_selection import closing_quotes
from app.ml.registry import upsert_run
from app.models.fixture import Fixture
from app.models.prediction import ModelRun, Prediction

logger = logging.getLogger("ml.training")
_OUTCOMES = ("home", "draw", "away")


@dataclass(slots=True)
class TrainingSummary:
    version: str
    trained: list[str] = field(default_factory=list)
    predictions_written: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    # Test-window metrics of the temporally evaluated methods (LightGBM, consensus).
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)


async def run_training(session: AsyncSession, *, version: str | None = None) -> TrainingSummary:
    version = version or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    summary = TrainingSummary(version=version)

    fixtures = list(
        (
            await session.execute(
                select(Fixture)
                .where(Fixture.ft_home.is_not(None))
                .order_by(Fixture.kickoff_at, Fixture.id)
            )
        )
        .scalars()
        .all()
    )
    if not fixtures:
        return summary

    feature_df = await build_feature_table(session)
    schema = feature_schema()
    data_hash = training_data_hash(feature_df)
    settings = get_settings()

    # --- Elo, Glicko-2 (running pre-match probabilities) --------------------
    elo_preds = _run_elo(fixtures)
    glicko_preds = _run_glicko(fixtures)
    dc_preds = _run_dixon_coles(fixtures)
    market_preds = _run_market(await _odds_map(session, fixtures))

    for method, preds, model in (
        (Method.elo, elo_preds, {"config": "football-elo"}),
        (Method.glicko2, glicko_preds, {"config": "glicko2"}),
        (Method.dixon_coles, dc_preds, {"config": "dixon-coles"}),
        (Method.market, market_preds, {"config": "shin-devig"}),
    ):
        if not preds:
            continue
        n = await _write_predictions(session, method.value, version, preds)
        summary.predictions_written += n
        run_id = log_training_run(
            method=method.value,
            version=version,
            model=model,
            feature_schema=schema,
            data_hash=data_hash,
            metrics=_prequential_metrics(preds, fixtures),
        )
        session.add(ModelRun(method=method.value, mlflow_run_id=run_id, status="done", metrics={}))
        await upsert_run(
            session,
            method=method.value,
            version=version,
            mlflow_run_id=run_id,
            sample_count=len(preds),
            min_samples=settings.champion_min_samples,
        )
        summary.trained.append(method.value)

    # --- LightGBM + consensus: temporal windows, test-window predictions ---
    lightgbm = train_lightgbm(feature_df)
    consensus = train_consensus(
        feature_df,
        {
            Method.elo.value: elo_preds,
            Method.glicko2.value: glicko_preds,
            Method.dixon_coles.value: dc_preds,
        },
    )
    for outcome in (lightgbm, consensus):
        if isinstance(outcome, MlSkipped):
            summary.skipped[outcome.method] = outcome.reason
            logger.warning(
                json.dumps(
                    {"event": "method_skipped", "method": outcome.method, "reason": outcome.reason}
                )
            )
            continue
        n = await _write_predictions(session, outcome.method, version, outcome.predictions)
        summary.predictions_written += n
        run_id = log_training_run(
            method=outcome.method,
            version=version,
            model=outcome.model,
            feature_schema=schema,
            data_hash=data_hash,
            metrics=outcome.metrics,
            params=outcome.params,
        )
        session.add(
            ModelRun(
                method=outcome.method, mlflow_run_id=run_id, status="done", metrics=outcome.metrics
            )
        )
        await upsert_run(
            session,
            method=outcome.method,
            version=version,
            mlflow_run_id=run_id,
            sample_count=outcome.sample_count,
            min_samples=settings.champion_min_samples,
        )
        summary.trained.append(outcome.method)
        summary.metrics[outcome.method] = outcome.metrics

    await session.flush()
    return summary


def _run_elo(fixtures: list[Fixture]) -> dict[uuid.UUID, dict[str, float]]:
    """Pre-match Elo probabilities. Each batch of same-kickoff fixtures is
    predicted from the ratings before the batch; the batch's rating changes
    (computed from those same pre-batch ratings) are applied afterwards."""
    elo = Elo()
    ratings: dict[uuid.UUID, float] = {}
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for batch in chronological_batches(fixtures):
        deltas: dict[uuid.UUID, float] = defaultdict(float)
        for fx in batch:
            rh = ratings.get(fx.home_team_id, DEFAULT_RATING)
            ra = ratings.get(fx.away_team_id, DEFAULT_RATING)
            preds[fx.id] = elo.prob_1x2(rh, ra)
            nh, na = elo.update(rh, ra, *finished_scores(fx))
            deltas[fx.home_team_id] += nh - rh
            deltas[fx.away_team_id] += na - ra
        for team, delta in deltas.items():
            ratings[team] = ratings.get(team, DEFAULT_RATING) + delta
    return preds


def _run_glicko(fixtures: list[Fixture]) -> dict[uuid.UUID, dict[str, float]]:
    """Pre-match Glicko-2 probabilities. A batch of same-kickoff fixtures is one
    rating period: predicted from the pre-batch states, then every team is
    updated once with all of its batch results against pre-batch opponents."""
    glicko = Glicko2()
    players: dict[uuid.UUID, GlickoPlayer] = {}
    splitter = Elo(EloConfig(home_advantage=30.0))
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for batch in chronological_batches(fixtures):
        results: dict[uuid.UUID, list[MatchResult]] = defaultdict(list)
        for fx in batch:
            ph = players.get(fx.home_team_id, GlickoPlayer())
            pa = players.get(fx.away_team_id, GlickoPlayer())
            preds[fx.id] = splitter.prob_1x2(ph.rating, pa.rating)
            ft_home, ft_away = finished_scores(fx)
            hs = 1.0 if ft_home > ft_away else (0.5 if ft_home == ft_away else 0.0)
            results[fx.home_team_id].append(MatchResult(pa.rating, pa.rd, hs))
            results[fx.away_team_id].append(MatchResult(ph.rating, ph.rd, 1.0 - hs))
        for team, team_results in results.items():
            players[team] = glicko.update(players.get(team, GlickoPlayer()), team_results)
    return preds


@dataclass(slots=True)
class _GoalStats:
    scored: int = 0
    conceded: int = 0
    matches: int = 0


def _run_dixon_coles(fixtures: list[Fixture]) -> dict[uuid.UUID, dict[str, float]]:
    """Pre-match Dixon-Coles probabilities from strictly earlier matches.

    Attack/defence come from running sufficient statistics (goals for/against
    and match counts per team, goals per team-match for the league) that are
    read before a batch and updated after it — never from the fixture being
    predicted. Same estimator as before: ``log((mean + 0.3) / league_avg)``; a
    team without history sits at the league average (strength 0). A
    likelihood-fitted Dixon-Coles with time decay is a later step (HANDOFF).
    """
    stats: dict[uuid.UUID, _GoalStats] = defaultdict(_GoalStats)
    league_goals = 0
    league_samples = 0
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for batch in chronological_batches(fixtures):
        league_avg = max(league_goals / league_samples if league_samples else 1.35, 0.2)
        attack: dict[str, float] = {}
        defence: dict[str, float] = {}
        for team in {fx.home_team_id for fx in batch} | {fx.away_team_id for fx in batch}:
            st = stats.get(team)
            if st is None or st.matches == 0:
                continue
            attack[str(team)] = float(np.log((st.scored / st.matches + 0.3) / league_avg))
            defence[str(team)] = float(np.log((st.conceded / st.matches + 0.3) / league_avg))
        dc = DixonColes(DixonColesParams(attack=attack, defence=defence))
        for fx in batch:
            preds[fx.id] = dc.predict_1x2(str(fx.home_team_id), str(fx.away_team_id))
        for fx in batch:
            ft_home, ft_away = finished_scores(fx)
            home, away = stats[fx.home_team_id], stats[fx.away_team_id]
            home.scored += ft_home
            home.conceded += ft_away
            home.matches += 1
            away.scored += ft_away
            away.conceded += ft_home
            away.matches += 1
            league_goals += ft_home + ft_away
            league_samples += 2
    return preds


async def _odds_map(
    session: AsyncSession, fixtures: list[Fixture]
) -> dict[uuid.UUID, dict[str, float]]:
    """Closing Pinnacle 1X2 quote per fixture (latest complete snapshot with
    ``ts <= kickoff``; see :mod:`app.ml.odds_selection`)."""
    quotes = await closing_quotes(session, fixtures, markets=("1x2",))
    return {fid: {k: float(v) for k, v in q.items()} for fid, q in quotes.items()}


def _run_market(odds: dict[uuid.UUID, dict[str, float]]) -> dict[uuid.UUID, dict[str, float]]:
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for fixture_id, prices in odds.items():
        if {"home", "draw", "away"} <= set(prices):
            probs = shin_devig([prices["home"], prices["draw"], prices["away"]])
            preds[fixture_id] = dict(zip(_OUTCOMES, probs, strict=True))
    return preds


def _label_from_scores(ft_home: int, ft_away: int) -> int:
    return 0 if ft_home > ft_away else (1 if ft_home == ft_away else 2)


def _prequential_metrics(
    preds: dict[uuid.UUID, dict[str, float]], fixtures: list[Fixture]
) -> dict[str, float]:
    label = {fx.id: _label_from_scores(*finished_scores(fx)) for fx in fixtures}
    rows = [(p, label[fid]) for fid, p in preds.items() if fid in label]
    if not rows:
        return {}
    probs = np.array([[p["home"], p["draw"], p["away"]] for p, _ in rows])
    y = np.array([lbl for _, lbl in rows])
    return {"prequential_brier": metrics_mod.brier_multiclass(probs, y)}


async def _write_predictions(
    session: AsyncSession, method: str, version: str, preds: dict[uuid.UUID, dict[str, float]]
) -> int:
    written = 0
    for fixture_id, probs in preds.items():
        for outcome in _OUTCOMES:
            stmt = (
                pg_insert(Prediction)
                .values(
                    fixture_id=fixture_id,
                    method=method,
                    market="1x2",
                    outcome=outcome,
                    probability=round(float(probs[outcome]), 5),
                    model_version=version,
                )
                .on_conflict_do_nothing(constraint="uq_prediction_identity")
                .returning(Prediction.id)
            )
            written += len((await session.execute(stmt)).fetchall())
    return written
