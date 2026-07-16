"""Provider account admin service (Phase 12a).

CRUD over ``provider_accounts`` for the admin dashboard. The API key is
encrypted at rest (Fernet, ``app.core.crypto``) exactly like every other stored
secret; only a masked suffix (last 4 chars) is ever returned, and the plaintext
is never logged.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.reference import ProviderAccount


def mask_key(suffix: str | None) -> str | None:
    """Render a stored key suffix for display, e.g. '••••1234'. Never the key."""
    return None if not suffix else f"••••{suffix}"


async def list_providers(session: AsyncSession) -> list[ProviderAccount]:
    return list(
        (await session.execute(select(ProviderAccount).order_by(ProviderAccount.priority)))
        .scalars()
        .all()
    )


async def get_provider(session: AsyncSession, provider_id: uuid.UUID) -> ProviderAccount | None:
    return await session.get(ProviderAccount, provider_id)


def _apply_api_key(provider: ProviderAccount, api_key: str | None) -> None:
    """Encrypt and store a new API key + its last-4 suffix. Plaintext is dropped."""
    if api_key:
        provider.encrypted_key = encrypt_secret(api_key)
        provider.key_suffix = api_key[-4:]


async def create_provider(session: AsyncSession, values: dict[str, Any]) -> ProviderAccount:
    api_key = values.pop("api_key", None)
    provider = ProviderAccount(**values)
    _apply_api_key(provider, api_key)
    session.add(provider)
    await session.flush()
    return provider


async def update_provider(
    session: AsyncSession, provider: ProviderAccount, changes: dict[str, Any]
) -> ProviderAccount:
    api_key = changes.pop("api_key", None)
    for field, value in changes.items():
        setattr(provider, field, value)
    _apply_api_key(provider, api_key)
    await session.flush()
    return provider


async def delete_provider(session: AsyncSession, provider: ProviderAccount) -> None:
    await session.delete(provider)


async def set_enabled(
    session: AsyncSession, provider: ProviderAccount, *, enabled: bool
) -> ProviderAccount:
    provider.is_enabled = enabled
    await session.flush()
    return provider
