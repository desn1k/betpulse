"""Model registry service (governance §16).

Populated after each training run (``upsert_run``) and re-evaluated nightly
(``apply_champion_selection``). Every governance change snapshots the FULL
registry state first, so ``rollback_to_snapshot`` restores it atomically.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_registry import ModelRegistry, ModelRegistrySnapshot, ModelStatus
from app.services.audit import record_event

CHAMPION_PROMOTED = "model.champion.promoted"
CHAMPION_DEMOTED = "model.champion.demoted"
REGISTRY_ROLLBACK = "model.registry.rollback"


@dataclass(slots=True)
class MethodMetrics:
    accuracy_pct: float
    brier: float
    log_loss: float
    roi_vs_closing: float
    sample_count: int


def _now() -> datetime:
    return datetime.now(UTC)


async def upsert_run(
    session: AsyncSession,
    *,
    method: str,
    version: str,
    mlflow_run_id: str | None,
    sample_count: int,
    min_samples: int,
) -> None:
    stmt = (
        pg_insert(ModelRegistry)
        .values(
            method=method,
            version=version,
            mlflow_run_id=mlflow_run_id,
            sample_count=sample_count,
            min_samples=min_samples,
            status=ModelStatus.challenger,
            last_trained_at=_now(),
            display_weight=Decimal("0"),
        )
        .on_conflict_do_update(
            constraint="uq_registry_method_version",
            set_={
                "mlflow_run_id": mlflow_run_id,
                "sample_count": sample_count,
                "last_trained_at": _now(),
            },
        )
    )
    await session.execute(stmt)
    await session.flush()


def _row_to_dict(row: ModelRegistry) -> dict[str, Any]:
    return {
        "method": row.method,
        "version": row.version,
        "mlflow_run_id": row.mlflow_run_id,
        "accuracy_pct": None if row.accuracy_pct is None else float(row.accuracy_pct),
        "brier": None if row.brier is None else float(row.brier),
        "log_loss": None if row.log_loss is None else float(row.log_loss),
        "roi_vs_closing": None if row.roi_vs_closing is None else float(row.roi_vs_closing),
        "sample_count": row.sample_count,
        "status": row.status.value,
        "is_enabled": row.is_enabled,
        "is_visible": row.is_visible,
        "display_weight": float(row.display_weight),
        "min_samples": row.min_samples,
        "notes": row.notes,
    }


async def snapshot_registry(
    session: AsyncSession, *, reason: str, actor: str | None = None
) -> ModelRegistrySnapshot:
    rows = (await session.execute(select(ModelRegistry))).scalars().all()
    snapshot = ModelRegistrySnapshot(
        reason=reason, actor=actor, payload=[_row_to_dict(r) for r in rows]
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def rollback_to_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> None:
    """Restore the full registry state captured in a snapshot, atomically."""
    snapshot = await session.get(ModelRegistrySnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError("snapshot not found")

    current = (await session.execute(select(ModelRegistry))).scalars().all()
    by_key = {(r.method, r.version): r for r in current}
    for item in snapshot.payload:
        row = by_key.get((item["method"], item["version"]))
        if row is None:
            continue
        row.status = ModelStatus(item["status"])
        row.is_enabled = item["is_enabled"]
        row.is_visible = item["is_visible"]
        row.display_weight = Decimal(str(item["display_weight"]))
        row.accuracy_pct = (
            None if item["accuracy_pct"] is None else Decimal(str(item["accuracy_pct"]))
        )
    await record_event(session, action=REGISTRY_ROLLBACK, target=str(snapshot_id))
    await session.flush()


def _softmax_weights(accuracies: dict[str, float]) -> dict[str, float]:
    if not accuracies:
        return {}
    mx = max(accuracies.values())
    exps = {m: math.exp((a - mx) / 10.0) for m, a in accuracies.items()}
    total = sum(exps.values())
    return {m: round(100.0 * e / total, 2) for m, e in exps.items()}


async def apply_champion_selection(
    session: AsyncSession,
    metrics_by_method: dict[str, MethodMetrics],
    *,
    weight_mode: str = "auto",
    min_samples: int = 300,
    actor: str = "system",
) -> str | None:
    """Update metrics, promote the best eligible method to champion (demoting the
    previous one), and set consensus weights. Idempotent: no change → no snapshot
    and no audit entry. Returns the champion method (or None)."""
    rows = {
        r.method: r
        for r in (await session.execute(select(ModelRegistry))).scalars().all()
        if r.method in metrics_by_method
    }
    for method, m in metrics_by_method.items():
        row = rows.get(method)
        if row is None:
            continue
        row.accuracy_pct = Decimal(str(round(m.accuracy_pct, 2)))
        row.brier = Decimal(str(round(m.brier, 6)))
        row.log_loss = Decimal(str(round(m.log_loss, 6)))
        row.roi_vs_closing = Decimal(str(round(m.roi_vs_closing, 4)))
        row.sample_count = m.sample_count
        row.last_evaluated_at = _now()

    eligible = {
        method: m.accuracy_pct
        for method, m in metrics_by_method.items()
        if rows.get(method) is not None
        and rows[method].is_enabled
        and m.sample_count >= min_samples
    }
    if not eligible:
        await session.flush()
        return None

    best = max(eligible, key=lambda k: eligible[k])
    current_champion = next(
        (r.method for r in rows.values() if r.status == ModelStatus.champion), None
    )

    if best != current_champion:
        await snapshot_registry(session, reason="champion_reeval", actor=actor)
        if current_champion is not None:
            rows[current_champion].status = ModelStatus.challenger
            await record_event(session, action=CHAMPION_DEMOTED, target=current_champion)
        rows[best].status = ModelStatus.champion
        await record_event(
            session,
            action=CHAMPION_PROMOTED,
            target=best,
            meta={"accuracy_pct": eligible[best]},
        )

    if weight_mode == "auto":
        weights = _softmax_weights({m: metrics_by_method[m].accuracy_pct for m in eligible})
        for method, row in rows.items():
            row.display_weight = Decimal(str(weights.get(method, 0.0)))

    await session.flush()
    return best
