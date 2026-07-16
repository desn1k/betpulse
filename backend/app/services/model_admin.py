"""Admin ML-governance orchestration (Phase 12b).

Thin layer over ``app.ml.registry`` for the admin dashboard: the runtime
weighting mode (``model_weighting`` singleton), manual weight editing, manual
promote/demote (snapshotting first, so it is reversible), and a rollback diff
preview. Auto mode derives weights from accuracy via the same softmax the nightly
re-eval uses; switching back to auto recomputes them immediately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.registry import (
    CHAMPION_DEMOTED,
    CHAMPION_PROMOTED,
    _softmax_weights,
    snapshot_registry,
)
from app.models.model_registry import ModelRegistry, ModelRegistrySnapshot, ModelStatus
from app.models.model_weighting import (
    MODEL_WEIGHTING_SINGLETON,
    ModelWeighting,
    WeightingMode,
)
from app.services.audit import record_event

MODEL_UPDATE = "model.update"
WEIGHTING_MODE_SET = "model.weighting.mode"
WEIGHTS_SET = "model.weights.set"

WEIGHT_SUM_TOLERANCE = 0.05


class WeightsInvalid(Exception):
    """Manual weights do not sum to 100."""


class NotManualMode(Exception):
    """Manual weight edits are only allowed while the mode is manual."""


# --- weighting mode ---------------------------------------------------------


async def get_weighting(session: AsyncSession) -> ModelWeighting:
    row = (
        await session.execute(
            select(ModelWeighting).where(ModelWeighting.singleton == MODEL_WEIGHTING_SINGLETON)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    await session.execute(
        pg_insert(ModelWeighting)
        .values(singleton=MODEL_WEIGHTING_SINGLETON)
        .on_conflict_do_nothing(index_elements=["singleton"])
    )
    await session.flush()
    return (
        await session.execute(
            select(ModelWeighting).where(ModelWeighting.singleton == MODEL_WEIGHTING_SINGLETON)
        )
    ).scalar_one()


async def _visible_rows(session: AsyncSession) -> list[ModelRegistry]:
    return list((await session.execute(select(ModelRegistry))).scalars().all())


async def recompute_auto_weights(session: AsyncSession) -> dict[str, float]:
    """Set ``display_weight`` from a softmax of accuracy over the visible methods
    that have an accuracy; everyone else gets 0. Returns the weights applied."""
    rows = await _visible_rows(session)
    accuracies = {
        r.method: float(r.accuracy_pct) for r in rows if r.is_visible and r.accuracy_pct is not None
    }
    weights = _softmax_weights(accuracies)
    for r in rows:
        r.display_weight = Decimal(str(weights.get(r.method, 0.0)))
    await session.flush()
    return weights


async def set_weighting_mode(session: AsyncSession, mode: WeightingMode) -> ModelWeighting:
    """Switch the weighting mode. Switching to ``auto`` recomputes and persists the
    softmax weights immediately (do not wait for the nightly re-eval)."""
    row = await get_weighting(session)
    row.mode = mode
    if mode == WeightingMode.auto:
        await recompute_auto_weights(session)
    await session.flush()
    return row


async def set_manual_weights(session: AsyncSession, weights: dict[str, float]) -> dict[str, float]:
    """Set per-method display weights (manual mode only). Weights must sum to 100."""
    if (await get_weighting(session)).mode != WeightingMode.manual:
        raise NotManualMode
    if abs(sum(weights.values()) - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise WeightsInvalid
    rows = await _visible_rows(session)
    for r in rows:
        if r.method in weights:
            r.display_weight = Decimal(str(round(weights[r.method], 2)))
    await session.flush()
    return weights


# --- promote / demote -------------------------------------------------------


@dataclass(slots=True)
class PromoteResult:
    promoted: bool
    warning: str | None


async def promote(session: AsyncSession, row_id: uuid.UUID, *, actor: str) -> PromoteResult | None:
    """Make ``row_id`` the champion (demoting the current one), snapshotting first.
    Allowed even below ``min_samples`` — the response carries a warning and the
    audit records ``override: true`` so it is traceable. None if the row is unknown."""
    row = await session.get(ModelRegistry, row_id)
    if row is None:
        return None

    below = row.sample_count < row.min_samples or row.accuracy_pct is None
    warning = "below_min_samples" if below else None

    await snapshot_registry(session, reason="manual_promote", actor=actor)
    current = (
        (
            await session.execute(
                select(ModelRegistry).where(ModelRegistry.status == ModelStatus.champion)
            )
        )
        .scalars()
        .all()
    )

    for champ in current:
        if champ.id != row.id:
            champ.status = ModelStatus.challenger
            await record_event(session, action=CHAMPION_DEMOTED, target=champ.method)
    row.status = ModelStatus.champion
    await record_event(
        session,
        action=CHAMPION_PROMOTED,
        target=row.method,
        meta={"override": below},
    )
    await session.flush()
    return PromoteResult(promoted=True, warning=warning)


async def demote(session: AsyncSession, row_id: uuid.UUID, *, actor: str) -> bool:
    """Demote a champion row back to challenger (snapshotting first). Returns False
    if the row is unknown; a no-op (True) if it was not champion."""
    row = await session.get(ModelRegistry, row_id)
    if row is None:
        return False
    if row.status == ModelStatus.champion:
        await snapshot_registry(session, reason="manual_demote", actor=actor)
        row.status = ModelStatus.challenger
        await record_event(session, action=CHAMPION_DEMOTED, target=row.method)
        await session.flush()
    return True


# --- snapshots + rollback diff ---------------------------------------------


async def list_snapshots(session: AsyncSession) -> list[ModelRegistrySnapshot]:
    return list(
        (
            await session.execute(
                select(ModelRegistrySnapshot).order_by(ModelRegistrySnapshot.taken_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def rollback_diff(session: AsyncSession, snapshot_id: uuid.UUID) -> list[dict[str, Any]]:
    """Preview what applying a snapshot would change: per method, the status and
    weight before (current) → after (snapshot). Only rows that actually change are
    returned, so the admin can confirm before rolling back."""
    snapshot = await session.get(ModelRegistrySnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError("snapshot not found")
    current = {
        (r.method, r.version): r
        for r in (await session.execute(select(ModelRegistry))).scalars().all()
    }
    changes: list[dict[str, Any]] = []
    for item in snapshot.payload:
        row = current.get((item["method"], item["version"]))
        if row is None:
            continue
        cur_status, new_status = row.status.value, item["status"]
        cur_weight, new_weight = float(row.display_weight), float(item["display_weight"])
        cur_enabled, new_enabled = row.is_enabled, item["is_enabled"]
        cur_visible, new_visible = row.is_visible, item["is_visible"]
        if (
            cur_status == new_status
            and abs(cur_weight - new_weight) < 0.01
            and cur_enabled == new_enabled
            and cur_visible == new_visible
        ):
            continue
        changes.append(
            {
                "method": item["method"],
                "version": item["version"],
                "status_from": cur_status,
                "status_to": new_status,
                "weight_from": round(cur_weight, 2),
                "weight_to": round(new_weight, 2),
                "enabled_from": cur_enabled,
                "enabled_to": new_enabled,
                "visible_from": cur_visible,
                "visible_to": new_visible,
            }
        )
    return changes
