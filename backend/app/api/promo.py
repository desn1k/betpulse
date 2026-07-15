"""Promo endpoints: admin batch management + user redemption (spec §7)."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.deps import (
    CurrentUser,
    get_client_ip,
    get_db,
    get_redis_dep,
    require_admin,
)
from app.models.promo import PromoBatch, PromoCode
from app.models.user import User
from app.schemas.promo import (
    BatchCreate,
    BatchCreateOut,
    BatchOut,
    KillOut,
    RedeemEffect,
    RedeemOut,
    RedeemRequest,
)
from app.services import promo as promo_service
from app.services.audit import record_event
from app.services.limits import RateLimited, enforce_promo_redeem_limit

admin_router = APIRouter(prefix="/admin/promo", tags=["promo-admin"])
router = APIRouter(prefix="/promo", tags=["promo"])


@admin_router.post("/batches", response_model=BatchCreateOut, status_code=status.HTTP_201_CREATED)
async def create_batch(
    payload: BatchCreate,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> BatchCreateOut:
    try:
        generated = await promo_service.generate_batch(
            session,
            name=payload.name,
            code_type=payload.code_type,
            size=payload.size,
            value=payload.value,
            tier_id=payload.tier_id,
            bound_user_id=payload.bound_user_id,
            max_activations=payload.max_activations,
            expires_at=payload.expires_at,
            stackable=payload.stackable,
            created_by=admin.id,
        )
    except promo_service.BatchSizeInvalid as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    await record_event(
        session,
        action="promo.batch.create",
        actor_user_id=admin.id,
        target=f"promo_batch:{generated.batch.id}",
        ip=get_client_ip(request),
        meta={"size": payload.size, "code_type": payload.code_type.value},
    )
    await session.commit()
    return BatchCreateOut(
        batch=BatchOut.model_validate(generated.batch), codes=generated.plaintext_codes
    )


@admin_router.get("/batches", response_model=list[BatchOut])
async def list_batches(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[PromoBatch]:
    return list(
        (await session.execute(select(PromoBatch).order_by(PromoBatch.created_at.desc())))
        .scalars()
        .all()
    )


@admin_router.post("/batches/{batch_id}/kill", response_model=KillOut)
async def kill_batch(
    batch_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> KillOut:
    try:
        disabled = await promo_service.kill_batch(session, batch_id)
    except promo_service.InvalidCode as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found"
        ) from exc
    await record_event(
        session,
        action="promo.batch.kill",
        actor_user_id=admin.id,
        target=f"promo_batch:{batch_id}",
        ip=get_client_ip(request),
        meta={"disabled_codes": disabled},
    )
    await session.commit()
    return KillOut(batch_id=batch_id, disabled_codes=disabled)


@admin_router.get("/batches/{batch_id}/export.csv")
async def export_batch_csv(
    batch_id: uuid.UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    # Metadata only — plaintext codes are never stored, so they cannot appear here.
    rows = (
        (
            await session.execute(
                select(PromoCode)
                .where(PromoCode.batch_id == batch_id)
                .order_by(PromoCode.created_at)
            )
        )
        .scalars()
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["code_id", "status", "activations_used", "bound_user_id", "created_at"])
    for c in rows:
        writer.writerow(
            [
                str(c.id),
                c.status.value,
                c.activations_used,
                str(c.bound_user_id) if c.bound_user_id else "",
                c.created_at.isoformat(),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="promo_batch_{batch_id}.csv"'},
    )


@router.post("/redeem", response_model=RedeemOut)
async def redeem_code(
    payload: RedeemRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedeemOut:
    try:
        await enforce_promo_redeem_limit(
            redis, user_id=user.id, limit=settings.rate_limit_promo_per_hour
        )
    except RateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many redemption attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    try:
        effect = await promo_service.redeem(session, user=user, code=payload.code)
    except promo_service.PromoError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    await session.commit()
    return RedeemOut(
        effect=RedeemEffect(type=effect.type, value=effect.value, status=effect.status)
    )
