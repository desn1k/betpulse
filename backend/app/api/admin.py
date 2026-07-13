"""Admin routes.

Phase 2 ships only a guarded ping so the admin gate (role + password-change +
2FA enforcement in :func:`app.core.deps.require_admin`) is testable. The real
admin dashboard lands in later phases.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import require_admin
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
async def ping(_admin: Annotated[User, Depends(require_admin)]) -> dict[str, str]:
    return {"status": "ok"}
