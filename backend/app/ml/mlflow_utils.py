"""MLflow logging helpers.

Every training run stores four things (spec §16/§17): the model binary, the
feature schema (column names + types), the training-data hash (sha256 of the
input DataFrame), and the metrics. In dev/prod MLflow proxies artifacts to MinIO
(S3); in CI/tests the tracking URI points at a temp directory.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

import joblib
import mlflow
import pandas as pd

from app.core.config import get_settings


def training_data_hash(df: pd.DataFrame) -> str:
    """Deterministic sha256 over the input DataFrame's bytes."""
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


def _configure() -> None:
    mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)


def log_training_run(
    *,
    method: str,
    version: str,
    model: Any,
    feature_schema: dict[str, str],
    data_hash: str,
    metrics: dict[str, float],
    params: dict[str, Any] | None = None,
) -> str:
    """Log one run and return its MLflow run_id."""
    _configure()
    mlflow.set_experiment(f"method:{method}")
    with mlflow.start_run() as run:
        mlflow.log_param("method", method)
        mlflow.log_param("version", version)
        mlflow.log_param("training_data_hash", data_hash)
        for key, value in (params or {}).items():
            mlflow.log_param(key, value)
        if metrics:
            mlflow.log_metrics(metrics)
        mlflow.log_dict(feature_schema, "feature_schema.json")
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.joblib"
            joblib.dump(model, model_path)
            mlflow.log_artifact(str(model_path), artifact_path="model")
        run_id: str = run.info.run_id
        return run_id
