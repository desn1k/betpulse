"""Health probe endpoints.

- ``/health``       — liveness: the process is up and can serve requests.
- ``/health/ready`` — readiness: the app is ready to receive traffic. In later
  phases this will also check Postgres and Redis connectivity; for now it only
  reports the process as ready so orchestration has a stable contract.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    environment: str
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]


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
async def readiness() -> ReadinessResponse:
    """Readiness probe.

    Dependency checks (Postgres, Redis) are added alongside those services in
    later phases; today the process being reachable is sufficient.
    """
    return ReadinessResponse(status="ready")
