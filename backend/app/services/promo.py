"""Promo-code generation and redemption (spec §7).

Codes are stored only as an HMAC-SHA256 ``code_hash`` keyed by
``DATA_ENCRYPTION_KEY`` (no new secret) — the plaintext is returned to the admin
exactly once at generation. Redemption claims an activation slot with a single
guarded ``UPDATE`` (``... WHERE activations_used < max_activations``) and checks
the row count, so two concurrent redemptions of a one-use code can never both
win. Hash comparisons use :func:`hmac.compare_digest`, never ``==``.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.promo import (
    PromoBatch,
    PromoBatchStatus,
    PromoCode,
    PromoCodeStatus,
    PromoCodeType,
    PromoRedemption,
    PromoRedemptionStatus,
)
from app.models.tier import Subscription, SubscriptionSource, Tier
from app.models.user import User

BATCH_MULTIPLE = 500
# Unambiguous alphabet (no 0/O/1/I) for human-typable codes.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GROUPS = 3
_GROUP_LEN = 4


class PromoError(Exception):
    """Base class for redemption failures. ``code`` maps to an HTTP status."""

    http_status = 400


class BatchSizeInvalid(PromoError):
    http_status = 422


class InvalidCode(PromoError):
    http_status = 404


class CodeExpired(PromoError):
    http_status = 410


class CodeDisabled(PromoError):
    http_status = 410


class CodeExhausted(PromoError):
    """No activation slots left (also the concurrent-loser case)."""

    http_status = 409


class AlreadyRedeemed(PromoError):
    http_status = 409


class NotYourCode(PromoError):
    http_status = 403


def _normalize(code: str) -> str:
    """Uppercase and strip separators so a code types the same with/without dashes."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_code(code: str) -> str:
    """HMAC-SHA256 of the normalized code, keyed by DATA_ENCRYPTION_KEY."""
    key = bytes.fromhex(get_settings().data_encryption_key)
    return hmac.new(key, _normalize(code).encode("utf-8"), sha256).hexdigest()


def hashes_equal(a: str, b: str) -> bool:
    """Constant-time hash comparison (never ``==``)."""
    return hmac.compare_digest(a, b)


def generate_code() -> str:
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN)) for _ in range(_GROUPS)]
    return "-".join(groups)


@dataclass(frozen=True)
class GeneratedBatch:
    batch: PromoBatch
    plaintext_codes: list[str]


async def generate_batch(
    session: AsyncSession,
    *,
    name: str,
    code_type: PromoCodeType,
    size: int,
    value: Decimal | None = None,
    tier_id: uuid.UUID | None = None,
    bound_user_id: uuid.UUID | None = None,
    max_activations: int = 1,
    expires_at: datetime | None = None,
    stackable: bool = False,
    created_by: uuid.UUID | None = None,
) -> GeneratedBatch:
    if size <= 0 or size % BATCH_MULTIPLE != 0:
        raise BatchSizeInvalid(f"size must be a positive multiple of {BATCH_MULTIPLE}")

    batch = PromoBatch(
        name=name,
        code_type=code_type,
        value=value,
        tier_id=tier_id,
        bound_user_id=bound_user_id,
        max_activations=max_activations,
        size=size,
        stackable=stackable,
        expires_at=expires_at,
        status=PromoBatchStatus.active,
        created_by=created_by,
    )
    session.add(batch)
    await session.flush()

    plaintext: list[str] = []
    seen: set[str] = set()
    while len(plaintext) < size:
        code = generate_code()
        h = hash_code(code)
        if h in seen:
            continue
        seen.add(h)
        plaintext.append(code)
        session.add(
            PromoCode(
                batch_id=batch.id,
                code_hash=h,
                activations_used=0,
                max_activations=max_activations,
                status=PromoCodeStatus.active,
            )
        )
    await session.flush()
    return GeneratedBatch(batch=batch, plaintext_codes=plaintext)


async def kill_batch(session: AsyncSession, batch_id: uuid.UUID) -> int:
    """Disable a batch and all its codes atomically (single UPDATE for the codes)."""
    batch = await session.get(PromoBatch, batch_id)
    if batch is None:
        raise InvalidCode("batch not found")
    batch.status = PromoBatchStatus.disabled
    result = await session.execute(
        update(PromoCode)
        .where(PromoCode.batch_id == batch_id)
        .values(status=PromoCodeStatus.disabled)
        .returning(PromoCode.id)
    )
    disabled = len(result.all())
    await session.flush()
    return disabled


@dataclass(frozen=True)
class RedemptionEffect:
    type: PromoCodeType
    value: Decimal | None
    status: PromoRedemptionStatus


async def redeem(session: AsyncSession, *, user: User, code: str) -> RedemptionEffect:
    code_hash = hash_code(code)
    row = (
        await session.execute(select(PromoCode).where(PromoCode.code_hash == code_hash))
    ).scalar_one_or_none()
    if row is None or not hashes_equal(row.code_hash, code_hash):
        raise InvalidCode("unknown code")

    batch = await session.get(PromoBatch, row.batch_id)
    if batch is None:  # FK guarantees a batch; defensive
        raise InvalidCode("unknown code")

    if batch.status is PromoBatchStatus.disabled or row.status is PromoCodeStatus.disabled:
        raise CodeDisabled("code disabled")
    if batch.expires_at is not None and batch.expires_at <= datetime.now(UTC):
        raise CodeExpired("code expired")
    if batch.bound_user_id is not None and batch.bound_user_id != user.id:
        raise NotYourCode("code bound to another user")
    if row.bound_user_id is not None and row.bound_user_id != user.id:
        raise NotYourCode("code bound to another user")

    # Atomically claim one activation slot. If no row matches, the code is spent
    # (or was just spent by a concurrent request) → 409, no read-then-write race.
    claim = await session.execute(
        update(PromoCode)
        .where(
            PromoCode.id == row.id,
            PromoCode.status == PromoCodeStatus.active,
            PromoCode.activations_used < PromoCode.max_activations,
        )
        .values(activations_used=PromoCode.activations_used + 1)
        .returning(PromoCode.activations_used, PromoCode.max_activations)
    )
    claimed = claim.first()
    if claimed is None:
        raise CodeExhausted("no activations left")
    used, max_act = claimed
    if used >= max_act:
        row.status = PromoCodeStatus.redeemed

    effect = await _apply_effect(session, user=user, batch=batch, code_hash=code_hash)

    try:
        await session.flush()
    except IntegrityError as exc:  # uq_redemption_user_code → already redeemed
        raise AlreadyRedeemed("code already redeemed by this user") from exc
    return effect


async def _apply_effect(
    session: AsyncSession, *, user: User, batch: PromoBatch, code_hash: str
) -> RedemptionEffect:
    now = datetime.now(UTC)

    if batch.code_type in (PromoCodeType.trial, PromoCodeType.upgrade):
        status = PromoRedemptionStatus.applied
        expires_at: datetime | None = None
        if batch.code_type is PromoCodeType.trial and batch.value is not None:
            expires_at = now + timedelta(days=int(batch.value))
        elif batch.code_type is PromoCodeType.upgrade:
            expires_at = batch.expires_at
        if batch.tier_id is not None:
            tier = await session.get(Tier, batch.tier_id)
            if tier is not None:
                # Upsert: redeeming a tier the user already holds extends/replaces
                # the grant rather than colliding on uq_subscription_user_tier.
                await session.execute(
                    pg_insert(Subscription)
                    .values(
                        user_id=user.id,
                        tier_id=tier.id,
                        source=SubscriptionSource.promo,
                        expires_at=expires_at,
                    )
                    .on_conflict_do_update(
                        constraint="uq_subscription_user_tier",
                        set_={"source": SubscriptionSource.promo, "expires_at": expires_at},
                    )
                )
    else:
        # percent / fixed: no payments yet — record a pending discount that the
        # billing seam reads at checkout.
        status = PromoRedemptionStatus.pending

    session.add(
        PromoRedemption(
            user_id=user.id,
            batch_id=batch.id,
            code_hash=code_hash,
            code_type=batch.code_type,
            value=batch.value,
            status=status,
        )
    )
    return RedemptionEffect(type=batch.code_type, value=batch.value, status=status)
