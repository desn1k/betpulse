"""Training pipeline.

Full path per method: build features → train → log to MLflow (binary +
feature_schema.json + training_data_hash + metrics) → write predictions →
upsert model_registry (+ a model_runs row).

Elo, Glicko-2, Dixon-Coles and the market benchmark train on any amount of data.
LightGBM and the consensus stack need a minimum sample count; on tiny datasets
(e.g. the CI fixture) they are **skipped with a logged note** rather than trained
on unusable data — the skip is recorded in ``TrainingSummary.skipped`` and
asserted by the fixture test.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ml import metrics as metrics_mod
from app.ml.base import Method
from app.ml.dixon_coles import DixonColes, DixonColesParams
from app.ml.elo import Elo, EloConfig
from app.ml.features import build_feature_table, feature_schema, finished_scores
from app.ml.glicko2 import Glicko2, GlickoPlayer, MatchResult
from app.ml.market import shin_devig
from app.ml.mlflow_utils import log_training_run, training_data_hash
from app.ml.registry import upsert_run
from app.models.fixture import Fixture
from app.models.market import Odds
from app.models.prediction import ModelRun, Prediction

logger = logging.getLogger("ml.training")
_OUTCOMES = ("home", "draw", "away")
_ML_MIN_SAMPLES = 200


@dataclass(slots=True)
class TrainingSummary:
    version: str
    trained: list[str] = field(default_factory=list)
    predictions_written: int = 0
    skipped: dict[str, str] = field(default_factory=dict)


async def run_training(session: AsyncSession, *, version: str | None = None) -> TrainingSummary:
    version = version or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    summary = TrainingSummary(version=version)

    fixtures = list(
        (
            await session.execute(
                select(Fixture).where(Fixture.ft_home.is_not(None)).order_by(Fixture.kickoff_at)
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
            metrics=_in_sample_metrics(preds, fixtures),
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

    # --- LightGBM + consensus: gated by sample size -------------------------
    for method in (Method.lightgbm, Method.consensus):
        if len(fixtures) < _ML_MIN_SAMPLES:
            reason = f"insufficient samples ({len(fixtures)} < {_ML_MIN_SAMPLES})"
            summary.skipped[method.value] = reason
            logger.warning(
                json.dumps({"event": "method_skipped", "method": method.value, "reason": reason})
            )

    await session.flush()
    return summary


def _run_elo(fixtures: list[Fixture]) -> dict[uuid.UUID, dict[str, float]]:
    elo = Elo()
    ratings: dict[uuid.UUID, float] = {}
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for fx in fixtures:
        rh = ratings.get(fx.home_team_id, 1500.0)
        ra = ratings.get(fx.away_team_id, 1500.0)
        preds[fx.id] = elo.prob_1x2(rh, ra)
        ft_home, ft_away = finished_scores(fx)
        nh, na = elo.update(rh, ra, ft_home, ft_away)
        ratings[fx.home_team_id], ratings[fx.away_team_id] = nh, na
    return preds


def _run_glicko(fixtures: list[Fixture]) -> dict[uuid.UUID, dict[str, float]]:
    glicko = Glicko2()
    players: dict[uuid.UUID, GlickoPlayer] = {}
    splitter = Elo(EloConfig(home_advantage=30.0))
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for fx in fixtures:
        ph = players.get(fx.home_team_id, GlickoPlayer())
        pa = players.get(fx.away_team_id, GlickoPlayer())
        preds[fx.id] = splitter.prob_1x2(ph.rating, pa.rating)
        ft_home, ft_away = finished_scores(fx)
        hs = 1.0 if ft_home > ft_away else (0.5 if ft_home == ft_away else 0.0)
        players[fx.home_team_id] = glicko.update(ph, [MatchResult(pa.rating, pa.rd, hs)])
        players[fx.away_team_id] = glicko.update(pa, [MatchResult(ph.rating, ph.rd, 1.0 - hs)])
    return preds


def _run_dixon_coles(fixtures: list[Fixture]) -> dict[uuid.UUID, dict[str, float]]:
    # Strengths are keyed by the team-id string so lookups match at predict time.
    gf: dict[str, list[int]] = {}
    ga: dict[str, list[int]] = {}
    for fx in fixtures:
        ft_home, ft_away = finished_scores(fx)
        h, a = str(fx.home_team_id), str(fx.away_team_id)
        gf.setdefault(h, []).append(ft_home)
        ga.setdefault(h, []).append(ft_away)
        gf.setdefault(a, []).append(ft_away)
        ga.setdefault(a, []).append(ft_home)

    all_goals = [g for vals in gf.values() for g in vals]
    league_avg = max(float(np.mean(all_goals)) if all_goals else 1.35, 0.2)
    attack = {t: float(np.log((np.mean(v) + 0.3) / league_avg)) for t, v in gf.items()}
    defence = {t: float(np.log((np.mean(v) + 0.3) / league_avg)) for t, v in ga.items()}
    dc = DixonColes(DixonColesParams(attack=attack, defence=defence))

    preds: dict[uuid.UUID, dict[str, float]] = {}
    for fx in fixtures:
        preds[fx.id] = dc.predict_1x2(str(fx.home_team_id), str(fx.away_team_id))
    return preds


async def _odds_map(
    session: AsyncSession, fixtures: list[Fixture]
) -> dict[uuid.UUID, dict[str, float]]:
    rows = (
        (
            await session.execute(
                select(Odds).where(Odds.market == "1x2", Odds.bookmaker == "pinnacle")
            )
        )
        .scalars()
        .all()
    )
    by_fixture: dict[uuid.UUID, dict[str, float]] = {}
    for o in rows:
        by_fixture.setdefault(o.fixture_id, {})[o.outcome] = float(o.price)
    return by_fixture


def _run_market(odds: dict[uuid.UUID, dict[str, float]]) -> dict[uuid.UUID, dict[str, float]]:
    preds: dict[uuid.UUID, dict[str, float]] = {}
    for fixture_id, prices in odds.items():
        if {"home", "draw", "away"} <= set(prices):
            probs = shin_devig([prices["home"], prices["draw"], prices["away"]])
            preds[fixture_id] = dict(zip(_OUTCOMES, probs, strict=True))
    return preds


def _label_from_scores(ft_home: int, ft_away: int) -> int:
    return 0 if ft_home > ft_away else (1 if ft_home == ft_away else 2)


def _in_sample_metrics(
    preds: dict[uuid.UUID, dict[str, float]], fixtures: list[Fixture]
) -> dict[str, float]:
    label = {fx.id: _label_from_scores(*finished_scores(fx)) for fx in fixtures}
    rows = [(p, label[fid]) for fid, p in preds.items() if fid in label]
    if not rows:
        return {}
    probs = np.array([[p["home"], p["draw"], p["away"]] for p, _ in rows])
    y = np.array([lbl for _, lbl in rows])
    return {"in_sample_brier": metrics_mod.brier_multiclass(probs, y)}


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
