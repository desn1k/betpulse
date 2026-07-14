"""Model registry: champion selection idempotency, weights, and rollback."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.ml.registry import (
    CHAMPION_PROMOTED,
    MethodMetrics,
    apply_champion_selection,
    snapshot_registry,
)
from app.models.audit_log import AuditLog
from app.models.model_registry import ModelRegistry, ModelStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(session: AsyncSession, methods: list[str]) -> None:
    for m in methods:
        session.add(
            ModelRegistry(
                method=m,
                version="v1",
                status=ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("0"),
                min_samples=1,
                sample_count=0,
            )
        )
    await session.flush()


def _metrics() -> dict[str, MethodMetrics]:
    return {
        "elo": MethodMetrics(
            accuracy_pct=50.0, brier=0.20, log_loss=1.0, roi_vs_closing=0.0, sample_count=100
        ),
        "dixon_coles": MethodMetrics(
            accuracy_pct=70.0, brier=0.15, log_loss=0.9, roi_vs_closing=0.0, sample_count=100
        ),
    }


@pytest.mark.asyncio
async def test_champion_selection_is_strictly_idempotent(session: AsyncSession) -> None:
    await _seed(session, ["elo", "dixon_coles"])
    metrics = _metrics()

    first = await apply_champion_selection(session, metrics, min_samples=1)
    second = await apply_champion_selection(session, metrics, min_samples=1)
    assert first == "dixon_coles"
    assert second == "dixon_coles"

    # Exactly ONE champion row and exactly ONE promotion audit entry.
    champions = await session.scalar(
        select(func.count())
        .select_from(ModelRegistry)
        .where(ModelRegistry.status == ModelStatus.champion)
    )
    promotions = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == CHAMPION_PROMOTED)
    )
    assert champions == 1
    assert promotions == 1


@pytest.mark.asyncio
async def test_auto_weights_sum_to_100(session: AsyncSession) -> None:
    await _seed(session, ["elo", "dixon_coles"])
    await apply_champion_selection(session, _metrics(), min_samples=1, weight_mode="auto")
    weights = [
        float(r.display_weight)
        for r in (await session.execute(select(ModelRegistry))).scalars().all()
    ]
    assert sum(weights) == pytest.approx(100.0, abs=0.05)


@pytest.mark.asyncio
async def test_snapshot_and_rollback_restore_state(session: AsyncSession) -> None:
    await _seed(session, ["elo", "dixon_coles"])
    await apply_champion_selection(session, _metrics(), min_samples=1)

    snap = await snapshot_registry(session, reason="manual")
    saved = {
        r.method: (r.status, float(r.display_weight))
        for r in (await session.execute(select(ModelRegistry))).scalars().all()
    }

    # Mutate weights + demote everyone, then roll back.
    for r in (await session.execute(select(ModelRegistry))).scalars().all():
        r.display_weight = Decimal("1.11")
        r.status = ModelStatus.challenger
    await session.flush()

    from app.ml.registry import rollback_to_snapshot

    await rollback_to_snapshot(session, snap.id)

    restored = {
        r.method: (r.status, float(r.display_weight))
        for r in (await session.execute(select(ModelRegistry))).scalars().all()
    }
    assert restored == saved
