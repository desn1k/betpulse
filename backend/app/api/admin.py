"""Admin routes.

The admin gate (role + password-change + 2FA enforcement) lives in
:func:`app.core.deps.require_admin`. Phase 7 adds tier management (spec §7:
tiers are admin-editable data); the full admin dashboard lands in Phase 12.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_client_ip, get_db, get_redis_dep, require_admin
from app.models.tier import Tier
from app.models.user import User
from app.schemas.tiers import TierOut, TierUpdate
from app.services.audit import record_event
from app.services.tiers import invalidate_tier_cache

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def ping(_admin: Annotated[User, Depends(require_admin)]) -> dict[str, str]:
    return {"status": "ok"}


@router.get("/tiers", response_model=list[TierOut])
async def list_tiers(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[Tier]:
    return list((await session.execute(select(Tier).order_by(Tier.sort_order))).scalars().all())


@router.patch("/tiers/{tier_id}", response_model=TierOut)
async def update_tier(
    tier_id: uuid.UUID,
    payload: TierUpdate,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> Tier:
    tier = await session.get(Tier, tier_id)
    if tier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tier not found")

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(tier, field, value)
    await session.flush()

    # Invalidate the resolved-tier cache so the edit takes effect on the next
    # request rather than after the 60s TTL.
    await invalidate_tier_cache(redis, tier.name)

    await record_event(
        session,
        action="tier.update",
        actor_user_id=admin.id,
        target=f"tier:{tier.name}",
        ip=get_client_ip(request),
        meta={"fields": sorted(changes.keys())},
    )
    await session.commit()
    return tier
