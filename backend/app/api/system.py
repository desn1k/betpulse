"""Admin system health, audit viewer and ops-alert endpoints (Phase 12d)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.deps import get_client_ip, get_db, get_redis_dep, require_admin
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.system import (
    AuditLogList,
    AuditLogRow,
    OpsAlertOut,
    OpsAlertRequest,
    SystemHealthOut,
)
from app.services import ops_alerts, system_health
from app.services.audit import record_event

router = APIRouter(prefix="/admin/system", tags=["admin-system"])
audit_router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])


@router.get("/health", response_model=SystemHealthOut)
async def read_system_health(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SystemHealthOut:
    return await system_health.build_system_health(session, redis, settings)


@router.post("/alerts/test", response_model=OpsAlertOut)
async def send_test_alert(
    payload: OpsAlertRequest,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OpsAlertOut:
    try:
        await ops_alerts.send_ops_alert(settings, payload.message)
    except ops_alerts.OpsAlertNotConfigured:
        return OpsAlertOut(status="not_configured", detail="Telegram ops alerts are not configured")
    except ops_alerts.OpsAlertDeliveryFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await record_event(
        session,
        action="ops_alert.test",
        actor_user_id=admin.id,
        target="telegram_alert",
        ip=get_client_ip(request),
        meta={"message_length": len(payload.message)},
    )
    await session.commit()
    return OpsAlertOut(status="sent")


@audit_router.get("", response_model=AuditLogList)
async def list_audit_logs(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    action: Annotated[str | None, Query(max_length=64)] = None,
    actor_user_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=320)] = None,
    target: Annotated[str | None, Query(max_length=255)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AuditLogList:
    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if actor_user_id:
        conditions.append(AuditLog.actor_user_id == actor_user_id)
    if target:
        conditions.append(AuditLog.target.ilike(f"%{target}%"))
    if date_from:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to:
        conditions.append(AuditLog.created_at <= date_to)
    if q:
        needle = f"%{q.lower()}%"
        conditions.append(
            or_(
                func.lower(User.email).like(needle),
                func.lower(AuditLog.action).like(needle),
            )
        )

    base = (
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(*conditions)
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(AuditLog.created_at.desc()).limit(per_page).offset((page - 1) * per_page)
        )
    ).all()
    return AuditLogList(
        events=[
            AuditLogRow(
                id=row.AuditLog.id,
                actor_user_id=row.AuditLog.actor_user_id,
                actor_email=row.email,
                action=row.AuditLog.action,
                target=row.AuditLog.target,
                ip=row.AuditLog.ip,
                user_agent=row.AuditLog.user_agent,
                meta=row.AuditLog.meta,
                created_at=row.AuditLog.created_at,
            )
            for row in rows
        ],
        total=total,
        page=page,
        per_page=per_page,
    )
