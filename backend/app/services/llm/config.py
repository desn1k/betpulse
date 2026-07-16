"""LLM config singleton access (spec §8).

The API key is encrypted at rest (Fernet, `app.core.crypto`) and only ever
returned to a client as a masked suffix (last 4 chars) — the full key is never
logged or serialized. There is exactly one config row.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_secret
from app.models.llm import LLM_CONFIG_SINGLETON, LlmConfig


async def get_config(session: AsyncSession) -> LlmConfig:
    """Return the singleton config row, creating an empty (disabled) one if absent."""
    row = (
        await session.execute(select(LlmConfig).where(LlmConfig.singleton == LLM_CONFIG_SINGLETON))
    ).scalar_one_or_none()
    if row is not None:
        return row
    await session.execute(
        pg_insert(LlmConfig)
        .values(singleton=LLM_CONFIG_SINGLETON)
        .on_conflict_do_nothing(index_elements=["singleton"])
    )
    await session.flush()
    return (
        await session.execute(select(LlmConfig).where(LlmConfig.singleton == LLM_CONFIG_SINGLETON))
    ).scalar_one()


def mask_key(suffix: str | None) -> str | None:
    """Render a stored key suffix for display, e.g. '••••1234'. Never the full key."""
    return None if not suffix else f"••••{suffix}"


async def update_config(session: AsyncSession, changes: dict[str, Any]) -> LlmConfig:
    """Apply a partial config update. A provided ``api_key`` is encrypted and its
    last-4 suffix stored; the plaintext is never persisted or logged."""
    config = await get_config(session)
    api_key = changes.pop("api_key", None)
    for field, value in changes.items():
        setattr(config, field, value)
    if api_key:
        config.encrypted_key = encrypt_secret(api_key)
        config.key_suffix = api_key[-4:]
    await session.flush()
    return config
