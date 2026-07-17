"""Admin user management endpoints (spec §9).

All routes are admin-gated (:func:`require_admin`) and every mutation is
audited. Manual tier grants go through ``Subscription(source=manual)``; disabling
a user revokes all their refresh tokens in the same transaction.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_client_ip, get_db, require_admin
from app.models.tier import Tier
from app.models.user import User
from app.schemas.user_admin import (
    DisableOut,
    RedemptionRow,
    TierAssign,
    UserList,
    UserMutationOut,
    UserRow,
)
from app.services import user_admin
from app.services.audit import record_event

admin_router = APIRouter(prefix="/admin/users", tags=["users-admin"])


def _row(item: user_admin.UserListItem) -> UserRow:
    u = item.user
    return UserRow(
        id=u.id,
        email=u.email,
        role=u.role,
        base_tier=u.tier,
        effective_tier=item.effective_tier,
        tier_expires_at=item.tier_expires_at,
        is_active=u.is_active,
        is_verified=u.is_verified,
        created_at=u.created_at,
    )


@admin_router.get("", response_model=UserList)
async def list_users(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    email: Annotated[str | None, Query(max_length=320)] = None,
    tier: Annotated[str | None, Query(max_length=32)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
) -> UserList:
    result = await user_admin.list_users(
        session, email=email, tier=tier, page=page, per_page=per_page
    )
    return UserList(
        users=[_row(i) for i in result.items],
        total=result.total,
        page=page,
        per_page=per_page,
    )


@admin_router.post("/{user_id}/tier", response_model=UserMutationOut)
async def assign_tier(
    user_id: uuid.UUID,
    payload: TierAssign,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserMutationOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    tier = await session.get(Tier, payload.tier_id)
    if tier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tier not found")

    assigned = await user_admin.assign_tier(
        session, user=user, tier=tier, expires_at=payload.expires_at
    )
    await record_event(
        session,
        action="user.tier.assign",
        actor_user_id=admin.id,
        target=f"user:{user.id}",
        ip=get_client_ip(request),
        meta={
            "tier": tier.name,
            "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
        },
    )
    await session.commit()
    return UserMutationOut(
        id=user.id,
        is_active=user.is_active,
        effective_tier=assigned.effective_tier,
        tier_expires_at=assigned.tier_expires_at,
    )


@admin_router.get("/{user_id}/redemptions", response_model=list[RedemptionRow])
async def list_redemptions(
    user_id: uuid.UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[RedemptionRow]:
    rows = await user_admin.list_redemptions(session, user_id=user_id)
    return [RedemptionRow.model_validate(r) for r in rows]


@admin_router.post("/{user_id}/disable", response_model=DisableOut)
async def disable_user(
    user_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DisableOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    revoked = await user_admin.disable_user(session, user=user)
    await record_event(
        session,
        action="user.disable",
        actor_user_id=admin.id,
        target=f"user:{user.id}",
        ip=get_client_ip(request),
        meta={"revoked_tokens": revoked},
    )
    await session.commit()
    return DisableOut(id=user.id, is_active=user.is_active, revoked_tokens=revoked)


@admin_router.post("/{user_id}/enable", response_model=UserMutationOut)
async def enable_user(
    user_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserMutationOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await user_admin.enable_user(session, user=user)
    effective, expires = await user_admin.resolve_effective(session, user)
    await record_event(
        session,
        action="user.enable",
        actor_user_id=admin.id,
        target=f"user:{user.id}",
        ip=get_client_ip(request),
        meta={},
    )
    await session.commit()
    return UserMutationOut(
        id=user.id,
        is_active=user.is_active,
        effective_tier=effective,
        tier_expires_at=expires,
    )
