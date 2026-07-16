"""Admin ingestion job-log + manual re-scan (Phase 12a).

Admin-only. The re-scan enqueues a historical football-data ingestion (one
``ingestion_runs`` row per league/season); progress is observed by polling the
runs list. Live polling runs on its own schedule and is not triggered here.
"""

from __future__ import annotations

from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.arq import get_arq_pool
from app.core.deps import get_client_ip, get_db, require_admin
from app.models.ingestion_run import IngestionRun, IngestionStatus
from app.models.user import User
from app.schemas.ingestion import (
    IngestionRunOut,
    IngestionRunsOut,
    RescanAccepted,
    RescanRequest,
)
from app.services.audit import record_event
from app.services.ingestion.football_data import LEAGUE_META

router = APIRouter(prefix="/admin/ingestion", tags=["admin-ingestion"])


@router.get("/runs", response_model=IngestionRunsOut)
async def list_runs(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    run_status: Annotated[IngestionStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
) -> IngestionRunsOut:
    conditions = [] if run_status is None else [IngestionRun.status == run_status]
    total = (
        await session.execute(select(func.count()).select_from(IngestionRun).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(IngestionRun)
                .where(*conditions)
                .order_by(IngestionRun.started_at.desc())
                .limit(per_page)
                .offset((page - 1) * per_page)
            )
        )
        .scalars()
        .all()
    )
    return IngestionRunsOut(
        runs=[IngestionRunOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/rescan", response_model=RescanAccepted, status_code=status.HTTP_202_ACCEPTED)
async def rescan(
    payload: RescanRequest,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    arq: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> RescanAccepted:
    unknown = [lg for lg in payload.leagues if lg not in LEAGUE_META]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "unknown_leagues", "leagues": unknown},
        )

    await arq.enqueue_job(
        "ingest_history_task", payload.leagues, payload.seasons, f"admin:{admin.id}"
    )
    await record_event(
        session,
        action="ingestion.rescan",
        actor_user_id=admin.id,
        target="ingestion",
        ip=get_client_ip(request),
        meta={"leagues": payload.leagues, "seasons": payload.seasons},
    )
    await session.commit()
    return RescanAccepted(leagues=payload.leagues, seasons=payload.seasons)
