"""Full training-pipeline path against the committed fixture.

Covers: features → train → MLflow log (binary + feature_schema + data hash) →
predictions written → model_registry upserted. LightGBM and consensus are
**skipped** on this tiny dataset (asserted below) and trained only on real data.
"""

from __future__ import annotations

from pathlib import Path

import mlflow
import pytest
from app.ml.training import run_training
from app.models.model_registry import ModelRegistry
from app.models.prediction import Prediction
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.services.ingestion.football_data import ingest_dtos
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE = Path(__file__).parent.parent / "fixtures" / "football_data" / "E0_2324.csv"


async def _ingest(session: AsyncSession) -> None:
    dtos = FootballDataCoUkProvider().parse_csv(FIXTURE.read_bytes(), "EPL", "2023-2024")
    await ingest_dtos(session, dtos, league_code="EPL")
    await session.flush()


@pytest.mark.asyncio
async def test_training_pipeline_full_path(session: AsyncSession) -> None:
    await _ingest(session)

    summary = await run_training(session, version="testver")

    # Methods that work on any data are trained; LightGBM + consensus are skipped.
    assert {"elo", "glicko2", "dixon_coles", "market"} <= set(summary.trained)
    assert "lightgbm" in summary.skipped and "consensus" in summary.skipped
    assert summary.predictions_written == 120  # 4 methods x 10 fixtures x 3 outcomes

    pred_count = await session.scalar(select(func.count()).select_from(Prediction))
    assert pred_count == 120

    registry = (await session.execute(select(ModelRegistry))).scalars().all()
    by_method = {r.method: r for r in registry}
    assert "elo" in by_method
    assert by_method["elo"].mlflow_run_id is not None
    assert by_method["elo"].last_trained_at is not None

    # The MLflow run carries the data hash and the required artifacts.
    client = mlflow.MlflowClient()
    run = client.get_run(by_method["elo"].mlflow_run_id)
    assert "training_data_hash" in run.data.params
    artifacts = {a.path for a in client.list_artifacts(run.info.run_id)}
    assert "feature_schema.json" in artifacts
    assert "model" in artifacts
