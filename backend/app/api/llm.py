"""LLM analysis endpoints (spec §8).

Public: ``GET /matches/{fixture_id}/analysis`` returns the cached (or freshly
generated) narrative for a match, gated by tier. The gate is a cheap DB lookup
on ``fixtures.fixture_llm_rank`` (computed daily by an ARQ cron):

* ``none`` (guest)         → always 403
* ``match_of_day`` (free)  → rank 1 only
* ``top5`` (pro)           → ranks 1–5
* ``any`` (expert)         → any fixture

Admin: ``GET/PATCH /admin/llm-config`` manages the singleton provider config.
The API key is encrypted at rest and only its masked suffix is ever returned;
the full key is never logged.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    TierContextDep,
    get_client_ip,
    get_db,
    get_redis_dep,
    require_admin,
)
from app.models.fixture import Fixture
from app.models.llm import LlmConfig
from app.models.user import User
from app.schemas.llm import AnalysisOut, Language, LlmConfigOut, LlmConfigUpdate
from app.services.audit import record_event
from app.services.llm.analysis import get_or_create_analysis
from app.services.llm.config import get_config, mask_key, update_config
from app.services.tiers import EXPERT, FREE, PRO

router = APIRouter(tags=["llm"])
admin_router = APIRouter(prefix="/admin", tags=["llm-admin"])


def _llm_allowed(access: str, rank: int | None) -> bool:
    """Whether an ``llm`` access level may view a fixture with this daily rank."""
    if access == "any":
        return True
    if access == "match_of_day":
        return rank == 1
    if access == "top5":
        return rank is not None and 1 <= rank <= 5
    return False  # "none" or unknown


def _required_tier_for(rank: int | None) -> str:
    """The lowest tier that unlocks LLM analysis for a fixture with this rank."""
    if rank == 1:
        return FREE
    if rank is not None and rank <= 5:
        return PRO
    return EXPERT


@router.get("/matches/{fixture_id}/analysis", response_model=AnalysisOut)
async def get_analysis(
    fixture_id: uuid.UUID,
    tier_ctx: TierContextDep,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    language: Annotated[Language, Query(description="Response language")] = "en",
) -> AnalysisOut:
    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    rank = fixture.fixture_llm_rank
    if not _llm_allowed(tier_ctx.tier.llm_access(), rank):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "llm_requires_upgrade", "tier_required": _required_tier_for(rank)},
        )

    result = await get_or_create_analysis(
        session, redis, fixture_id=fixture_id, language=language
    )
    await session.commit()
    return AnalysisOut(
        status=result.status,
        content=result.content,
        model=result.model,
        language=result.language,
        cached=result.cached,
        not_a_probability_source=result.not_a_probability_source,
        resets_at=result.resets_at,
        is_match_of_the_day=rank == 1,
    )


def _config_out(config: LlmConfig) -> LlmConfigOut:
    out = LlmConfigOut.model_validate(config)
    out.key_masked = mask_key(config.key_suffix)
    return out


@admin_router.get("/llm-config", response_model=LlmConfigOut)
async def read_llm_config(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LlmConfigOut:
    config = await get_config(session)
    await session.commit()
    return _config_out(config)


@admin_router.patch("/llm-config", response_model=LlmConfigOut)
async def patch_llm_config(
    payload: LlmConfigUpdate,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LlmConfigOut:
    changes = payload.model_dump(exclude_unset=True)
    # Snapshot the field names before update_config pops ``api_key`` off the dict.
    touched = sorted(changes.keys())
    config = await update_config(session, changes)

    # Audit the fields touched — never the secret value itself.
    await record_event(
        session,
        action="llm_config.update",
        actor_user_id=admin.id,
        target="llm_config",
        ip=get_client_ip(request),
        meta={"fields": touched},
    )
    await session.commit()
    return _config_out(config)
