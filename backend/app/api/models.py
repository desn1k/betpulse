"""Admin ML model management (Phase 12b, spec §16).

Admin-only governance over the model registry: view metrics, toggle
enabled/visible, edit consensus weights (auto softmax vs manual, sum = 100),
promote/demote a champion, retrain, and roll back to a snapshot (with a diff
preview). Every mutation snapshots first where relevant and is audited.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.arq import get_arq_pool
from app.core.deps import get_client_ip, get_db, require_admin
from app.ml.registry import rollback_to_snapshot
from app.models.model_registry import ModelRegistry
from app.models.user import User
from app.schemas.models import (
    ModelOut,
    ModelsOut,
    ModelUpdate,
    PromoteOut,
    RollbackDiffOut,
    SnapshotOut,
    WeightingModeIn,
    WeightsIn,
)
from app.services import model_admin
from app.services.audit import record_event

router = APIRouter(prefix="/admin/models", tags=["admin-models"])


@router.get("", response_model=ModelsOut)
async def list_models(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ModelsOut:
    rows = (
        (await session.execute(select(ModelRegistry).order_by(ModelRegistry.method)))
        .scalars()
        .all()
    )
    weighting = await model_admin.get_weighting(session)
    return ModelsOut(
        models=[ModelOut.model_validate(r) for r in rows], weighting_mode=weighting.mode
    )


@router.patch("/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: uuid.UUID,
    payload: ModelUpdate,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ModelOut:
    row = await session.get(ModelRegistry, model_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)
    await session.flush()
    await record_event(
        session,
        action=model_admin.MODEL_UPDATE,
        actor_user_id=admin.id,
        target=row.method,
        ip=get_client_ip(request),
        meta={"fields": sorted(changes.keys())},
    )
    await session.commit()
    return ModelOut.model_validate(row)


@router.put("/weighting", response_model=ModelsOut)
async def set_weighting_mode(
    payload: WeightingModeIn,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ModelsOut:
    await model_admin.set_weighting_mode(session, payload.mode)
    await record_event(
        session,
        action=model_admin.WEIGHTING_MODE_SET,
        actor_user_id=admin.id,
        target="model_weighting",
        ip=get_client_ip(request),
        meta={"mode": payload.mode.value},
    )
    await session.commit()
    return await list_models(admin, session)


@router.put("/weights", response_model=ModelsOut)
async def set_weights(
    payload: WeightsIn,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ModelsOut:
    try:
        await model_admin.set_manual_weights(session, payload.weights)
    except model_admin.NotManualMode as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "not_manual_mode"},
        ) from exc
    except model_admin.WeightsInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "weights_must_sum_to_100"},
        ) from exc
    await record_event(
        session,
        action=model_admin.WEIGHTS_SET,
        actor_user_id=admin.id,
        target="model_weighting",
        ip=get_client_ip(request),
        meta={"methods": sorted(payload.weights.keys())},
    )
    await session.commit()
    return await list_models(admin, session)


@router.post("/{model_id}/promote", response_model=PromoteOut)
async def promote_model(
    model_id: uuid.UUID,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PromoteOut:
    result = await model_admin.promote(session, model_id, actor=f"admin:{admin.id}")
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    await session.commit()
    return PromoteOut(promoted=result.promoted, warning=result.warning)


@router.post("/{model_id}/demote", status_code=status.HTTP_204_NO_CONTENT)
async def demote_model(
    model_id: uuid.UUID,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    ok = await model_admin.demote(session, model_id, actor=f"admin:{admin.id}")
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    await session.commit()


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[SnapshotOut]:
    return [SnapshotOut.model_validate(s) for s in await model_admin.list_snapshots(session)]


@router.get("/snapshots/{snapshot_id}/diff", response_model=RollbackDiffOut)
async def snapshot_diff(
    snapshot_id: uuid.UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> RollbackDiffOut:
    try:
        changes = await model_admin.rollback_diff(session, snapshot_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found"
        ) from exc
    return RollbackDiffOut(changes=changes)


@router.post("/rollback/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rollback(
    snapshot_id: uuid.UUID,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    try:
        await rollback_to_snapshot(session, snapshot_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found"
        ) from exc
    await session.commit()


@router.post("/retrain", status_code=status.HTTP_202_ACCEPTED)
async def retrain(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    arq: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> dict[str, bool]:
    await arq.enqueue_job("train_all_task")
    await record_event(
        session,
        action="model.retrain",
        actor_user_id=admin.id,
        target="model_registry",
        ip=get_client_ip(request),
    )
    await session.commit()
    return {"accepted": True}
