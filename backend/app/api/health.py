"""Health probe endpoints.

- ``/health``       — liveness: the process is up and can serve requests.
- ``/health/ready`` — readiness: Postgres and Redis are reachable, so the app
  can receive traffic.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_db, get_redis_dep
from app.schemas.system import ComponentHealth
from app.services.system_health import check_database, check_redis

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    environment: str
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    components: list[ComponentHealth]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        version="0.1.0",
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> ReadinessResponse:
    """Readiness probe backed by core dependency checks."""
    components = [await check_database(session), await check_redis(redis)]
    ready = all(component.status == "ok" for component in components)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", components=components)
