"""Tier defaults, seeding and resolution (spec §7).

The DB is the source of truth for tiers (an admin edits ``feature_flags`` /
``limits`` at runtime). ``DEFAULT_TIERS`` below seeds those rows and also serves
as a safe fallback when a row is missing, so resolution never fails closed in a
way that breaks reads. Resolved tiers are cached in Redis for a short TTL; the
admin PATCH invalidates the cache so edits take effect within seconds.

Feature-flag vocabulary (all authorization is server-side; the frontend only
mirrors these for UX):

* ``methods``: how much of the model breakdown is exposed on the match card —
  ``blurred_consensus`` (guest) · ``consensus`` (free) · ``all`` (pro) ·
  ``all_weights`` (expert, adds per-method consensus weights).
* ``per_half_totals`` / ``live_recompute``: booleans.

Limit vocabulary (``-1`` = unlimited):

* ``matches_per_day``: distinct match-detail views per UTC day.
* ``pushes_per_day`` / ``backtester_runs_per_day``: reserved for later phases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tier import Subscription, Tier
from app.models.user import User

GUEST = "guest"
FREE = "free"
PRO = "pro"
EXPERT = "expert"

_CACHE_TTL_SECONDS = 60
_CACHE_PREFIX = "tier:"


@dataclass(frozen=True)
class TierDefaults:
    price: float
    period: str | None
    feature_flags: dict[str, Any]
    limits: dict[str, Any]
    is_public: bool
    sort_order: int


DEFAULT_TIERS: dict[str, TierDefaults] = {
    GUEST: TierDefaults(
        price=0.0,
        period=None,
        feature_flags={
            "methods": "blurred_consensus",
            "per_half_totals": False,
            "live_recompute": False,
        },
        limits={"matches_per_day": 3, "pushes_per_day": 0, "backtester_runs_per_day": 0},
        is_public=False,
        sort_order=0,
    ),
    FREE: TierDefaults(
        price=0.0,
        period=None,
        feature_flags={"methods": "consensus", "per_half_totals": False, "live_recompute": False},
        limits={"matches_per_day": 10, "pushes_per_day": 1, "backtester_runs_per_day": 3},
        is_public=True,
        sort_order=1,
    ),
    PRO: TierDefaults(
        price=9.99,
        period="month",
        feature_flags={"methods": "all", "per_half_totals": True, "live_recompute": True},
        limits={"matches_per_day": -1, "pushes_per_day": 10, "backtester_runs_per_day": 50},
        is_public=True,
        sort_order=2,
    ),
    EXPERT: TierDefaults(
        price=19.99,
        period="month",
        feature_flags={"methods": "all_weights", "per_half_totals": True, "live_recompute": True},
        limits={"matches_per_day": -1, "pushes_per_day": -1, "backtester_runs_per_day": -1},
        is_public=True,
        sort_order=3,
    ),
}


@dataclass(frozen=True)
class ResolvedTier:
    """A tier's effective configuration used for authorization decisions."""

    name: str
    feature_flags: dict[str, Any]
    limits: dict[str, Any]

    def matches_per_day(self) -> int:
        value = self.limits.get("matches_per_day", 0)
        return int(value) if isinstance(value, (int, float)) else 0

    def methods_visibility(self) -> str:
        return str(self.feature_flags.get("methods", "blurred_consensus"))

    def shows_method_bars(self) -> bool:
        return self.methods_visibility() in ("all", "all_weights")

    def shows_weights(self) -> bool:
        return self.methods_visibility() == "all_weights"


async def seed_default_tiers(session: AsyncSession) -> None:
    """Idempotently upsert the default tier rows. Existing rows are left as-is
    (an admin may have edited them); only missing tiers are inserted."""
    for name, d in DEFAULT_TIERS.items():
        stmt = (
            pg_insert(Tier)
            .values(
                name=name,
                price=d.price,
                period=d.period,
                feature_flags=d.feature_flags,
                limits=d.limits,
                is_public=d.is_public,
                sort_order=d.sort_order,
            )
            .on_conflict_do_nothing(index_elements=["name"])
        )
        await session.execute(stmt)


def _default_resolved(name: str) -> ResolvedTier:
    d = DEFAULT_TIERS.get(name, DEFAULT_TIERS[GUEST])
    return ResolvedTier(name=name, feature_flags=dict(d.feature_flags), limits=dict(d.limits))


async def get_resolved_tier(session: AsyncSession, redis: Redis, name: str) -> ResolvedTier:
    """Resolve a tier by name: Redis cache → DB row → code default fallback."""
    cache_key = f"{_CACHE_PREFIX}{name}"
    cached = await redis.get(cache_key)
    if cached is not None:
        payload = json.loads(cached)
        return ResolvedTier(
            name=name, feature_flags=payload["feature_flags"], limits=payload["limits"]
        )

    row = (await session.execute(select(Tier).where(Tier.name == name))).scalar_one_or_none()
    resolved = (
        ResolvedTier(name=name, feature_flags=dict(row.feature_flags), limits=dict(row.limits))
        if row is not None
        else _default_resolved(name)
    )
    await redis.set(
        cache_key,
        json.dumps({"feature_flags": resolved.feature_flags, "limits": resolved.limits}),
        ex=_CACHE_TTL_SECONDS,
    )
    return resolved


async def invalidate_tier_cache(redis: Redis, name: str) -> None:
    await redis.delete(f"{_CACHE_PREFIX}{name}")


async def resolve_effective_tier_name(session: AsyncSession, user: User | None) -> str:
    """The tier name that applies to this caller.

    Precedence: the most privileged **active** (non-expired) subscription →
    otherwise the user's base ``users.tier`` → otherwise ``guest`` (anonymous).
    """
    if user is None:
        return GUEST

    now = datetime.now(UTC)
    best = (
        await session.execute(
            select(Tier.name)
            .join(Subscription, Subscription.tier_id == Tier.id)
            .where(
                Subscription.user_id == user.id,
                or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
            )
            .order_by(Tier.sort_order.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if best is not None:
        return best
    return user.tier.value


async def resolve_tier_context(
    session: AsyncSession, redis: Redis, user: User | None
) -> ResolvedTier:
    """Full tier resolution for a caller: effective name → resolved config."""
    name = await resolve_effective_tier_name(session, user)
    return await get_resolved_tier(session, redis, name)
