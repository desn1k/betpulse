"""Admin provider-account management (Phase 12a).

CRUD + enable/disable over ``provider_accounts``. Admin-only (``require_admin``);
every mutation is audited. The API key is write-only — only a masked suffix is
returned, the plaintext is never logged.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_client_ip, get_db, require_admin
from app.models.reference import ProviderAccount
from app.models.user import User
from app.schemas.providers import ProviderCreate, ProviderOut, ProviderUpdate
from app.services import providers as providers_service
from app.services.audit import record_event

router = APIRouter(prefix="/admin/providers", tags=["admin-providers"])


def _out(provider: ProviderAccount) -> ProviderOut:
    result = ProviderOut.model_validate(provider)
    result.key_masked = providers_service.mask_key(provider.key_suffix)
    return result


async def _get_or_404(session: AsyncSession, provider_id: uuid.UUID) -> ProviderAccount:
    provider = await providers_service.get_provider(session, provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return provider


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[ProviderOut]:
    return [_out(p) for p in await providers_service.list_providers(session)]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderCreate,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderOut:
    values = payload.model_dump(exclude_none=True)
    if "roles" in values:
        values["roles"] = [str(r) for r in values["roles"]]
    provider = await providers_service.create_provider(session, values)
    await record_event(
        session,
        action="provider.create",
        actor_user_id=admin.id,
        target=f"provider:{provider.name}",
        ip=get_client_ip(request),
        meta={"roles": provider.roles},
    )
    await session.commit()
    return _out(provider)


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderOut:
    provider = await _get_or_404(session, provider_id)
    changes = payload.model_dump(exclude_unset=True)
    if "roles" in changes and changes["roles"] is not None:
        changes["roles"] = [str(r) for r in changes["roles"]]
    # Snapshot field names before update_provider pops api_key; never log the value.
    touched = sorted(changes.keys())
    provider = await providers_service.update_provider(session, provider, changes)
    await record_event(
        session,
        action="provider.update",
        actor_user_id=admin.id,
        target=f"provider:{provider.name}",
        ip=get_client_ip(request),
        meta={"fields": touched},
    )
    await session.commit()
    return _out(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    provider = await _get_or_404(session, provider_id)
    name = provider.name
    await providers_service.delete_provider(session, provider)
    await record_event(
        session,
        action="provider.delete",
        actor_user_id=admin.id,
        target=f"provider:{name}",
        ip=get_client_ip(request),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{provider_id}/enable", response_model=ProviderOut)
async def enable_provider(
    provider_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderOut:
    return await _set_enabled(provider_id, request, admin, session, enabled=True)


@router.post("/{provider_id}/disable", response_model=ProviderOut)
async def disable_provider(
    provider_id: uuid.UUID,
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ProviderOut:
    return await _set_enabled(provider_id, request, admin, session, enabled=False)


async def _set_enabled(
    provider_id: uuid.UUID,
    request: Request,
    admin: User,
    session: AsyncSession,
    *,
    enabled: bool,
) -> ProviderOut:
    provider = await _get_or_404(session, provider_id)
    await providers_service.set_enabled(session, provider, enabled=enabled)
    await record_event(
        session,
        action="provider.enable" if enabled else "provider.disable",
        actor_user_id=admin.id,
        target=f"provider:{provider.name}",
        ip=get_client_ip(request),
    )
    await session.commit()
    return _out(provider)
