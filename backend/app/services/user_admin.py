"""Admin user management (spec §9).

The list resolves each user's **effective tier** in SQL — the most-privileged
active (non-expired) subscription, falling back to the base ``users.tier`` — so
the tier filter and pagination stay consistent (no post-filtering that would
break page counts). Manual tier grants create a ``source=manual`` subscription
(never touching ``users.tier`` directly). Disabling a user revokes every one of
their refresh tokens in the same transaction as clearing ``is_active`` — a
15-minute access-token window is too long when the disable is a security action.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, String, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.models.tier import Subscription, SubscriptionSource, Tier
from app.models.user import User


@dataclass(frozen=True)
class UserListItem:
    user: User
    effective_tier: str
    tier_expires_at: datetime | None


@dataclass(frozen=True)
class UserListResult:
    items: list[UserListItem]
    total: int


def _best_subscription_subquery(now: datetime):  # type: ignore[no-untyped-def]
    """Per-user most-privileged active subscription (tier name + expiry).

    ``row_number`` over ``(user_id ORDER BY tier.sort_order DESC)`` keeps exactly
    the highest-ranked active subscription for each user.
    """
    ranked = (
        select(
            Subscription.user_id.label("user_id"),
            Tier.name.label("tier_name"),
            Subscription.expires_at.label("expires_at"),
            func.row_number()
            .over(
                partition_by=Subscription.user_id,
                order_by=Tier.sort_order.desc(),
            )
            .label("rn"),
        )
        .join(Tier, Tier.id == Subscription.tier_id)
        .where(or_(Subscription.expires_at.is_(None), Subscription.expires_at > now))
        .subquery()
    )
    return (
        select(ranked.c.user_id, ranked.c.tier_name, ranked.c.expires_at)
        .where(ranked.c.rn == 1)
        .subquery()
    )


async def list_users(
    session: AsyncSession,
    *,
    email: str | None = None,
    tier: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> UserListResult:
    now = datetime.now(UTC)
    best = _best_subscription_subquery(now)
    # Effective tier = active subscription's tier, else the base users.tier.
    effective = func.coalesce(best.c.tier_name, cast(User.tier, String))

    filters: list[ColumnElement[bool]] = []
    if email:
        filters.append(User.email.ilike(f"%{email}%"))
    if tier:
        filters.append(effective == tier)

    base = select(User.id).outerjoin(best, best.c.user_id == User.id).where(*filters)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await session.execute(
            select(User, best.c.tier_name, best.c.expires_at, effective.label("effective"))
            .outerjoin(best, best.c.user_id == User.id)
            .where(*filters)
            .order_by(User.created_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
    ).all()

    items = [
        UserListItem(user=r[0], effective_tier=r.effective, tier_expires_at=r[2]) for r in rows
    ]
    return UserListResult(items=items, total=int(total))


@dataclass(frozen=True)
class AssignedTier:
    effective_tier: str
    tier_expires_at: datetime | None


async def assign_tier(
    session: AsyncSession,
    *,
    user: User,
    tier: Tier,
    expires_at: datetime | None,
) -> AssignedTier:
    """Upsert a ``source=manual`` subscription for the user. Re-granting a tier
    the user already holds updates its expiry rather than colliding on
    ``uq_subscription_user_tier``."""
    await session.execute(
        pg_insert(Subscription)
        .values(
            user_id=user.id,
            tier_id=tier.id,
            source=SubscriptionSource.manual,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            constraint="uq_subscription_user_tier",
            set_={"source": SubscriptionSource.manual, "expires_at": expires_at},
        )
    )
    await session.flush()
    resolved = await resolve_effective(session, user)
    return AssignedTier(effective_tier=resolved[0], tier_expires_at=resolved[1])


async def resolve_effective(session: AsyncSession, user: User) -> tuple[str, datetime | None]:
    """The user's effective tier name and its expiry (None for a base-tier user)."""
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(Tier.name, Subscription.expires_at)
            .join(Subscription, Subscription.tier_id == Tier.id)
            .where(
                Subscription.user_id == user.id,
                or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
            )
            .order_by(Tier.sort_order.desc())
            .limit(1)
        )
    ).first()
    if row is not None:
        return row[0], row[1]
    return user.tier.value, None


async def disable_user(session: AsyncSession, *, user: User) -> int:
    """Deactivate the account and revoke every refresh token in one transaction.

    Returns the number of tokens revoked. The caller commits.
    """
    user.is_active = False
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
        .returning(RefreshToken.id)
    )
    revoked = len(result.all())
    await session.flush()
    return revoked


async def enable_user(session: AsyncSession, *, user: User) -> None:
    user.is_active = True
    await session.flush()


async def list_redemptions(session: AsyncSession, *, user_id: uuid.UUID) -> list:  # type: ignore[type-arg]
    from app.models.promo import PromoRedemption

    return list(
        (
            await session.execute(
                select(PromoRedemption)
                .where(PromoRedemption.user_id == user_id)
                .order_by(PromoRedemption.redeemed_at.desc())
            )
        )
        .scalars()
        .all()
    )
