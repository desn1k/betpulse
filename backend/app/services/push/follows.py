"""Per-match follow (Phase 11).

A user follows a fixture to receive its probability-swing pushes; the swing push
dispatcher targets only a fixture's followers, so nobody is spammed for matches
they did not opt into. Follows are idempotent (unique on ``(user, fixture)``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live import PushFollow


async def follow_fixture(
    session: AsyncSession, *, user_id: uuid.UUID, fixture_id: uuid.UUID
) -> None:
    await session.execute(
        pg_insert(PushFollow)
        .values(user_id=user_id, fixture_id=fixture_id)
        .on_conflict_do_nothing(constraint="uq_push_follow")
    )


async def unfollow_fixture(
    session: AsyncSession, *, user_id: uuid.UUID, fixture_id: uuid.UUID
) -> None:
    await session.execute(
        delete(PushFollow).where(PushFollow.user_id == user_id, PushFollow.fixture_id == fixture_id)
    )


async def is_following(session: AsyncSession, *, user_id: uuid.UUID, fixture_id: uuid.UUID) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(PushFollow.user_id == user_id, PushFollow.fixture_id == fixture_id)
            )
        )
    )


async def followed_fixture_ids(session: AsyncSession, *, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await session.execute(select(PushFollow.fixture_id).where(PushFollow.user_id == user_id))
    ).scalars()
    return list(rows)
