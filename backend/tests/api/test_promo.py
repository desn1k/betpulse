"""Promo generation, redemption, limits and admin management (spec §7)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.security import create_access_token
from app.models.promo import (
    PromoBatch,
    PromoCode,
    PromoCodeStatus,
    PromoCodeType,
    PromoRedemption,
)
from app.models.tier import Subscription, Tier
from app.models.user import User, UserRole, UserTier
from app.services.promo import hash_code
from app.services.tiers import PRO, seed_default_tiers
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _user(session: AsyncSession, *, admin: bool = False) -> tuple[User, dict[str, str]]:
    user = User(
        email=f"{uuid.uuid4()}@x.com",
        password_hash="x",
        role=UserRole.admin if admin else UserRole.user,
        tier=UserTier.free,
        must_change_password=False,
        totp_enabled=admin,
    )
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return user, {"Authorization": f"Bearer {token}"}


async def _make_code(
    session: AsyncSession,
    *,
    code: str = "ABCD-EFGH-JKLM",
    code_type: PromoCodeType = PromoCodeType.trial,
    value: Decimal | None = Decimal("7"),
    tier_id: uuid.UUID | None = None,
    max_activations: int = 1,
    bound_user_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> tuple[PromoBatch, PromoCode]:
    batch = PromoBatch(
        name="batch",
        code_type=code_type,
        value=value,
        tier_id=tier_id,
        bound_user_id=bound_user_id,
        max_activations=max_activations,
        size=500,
        expires_at=expires_at,
    )
    session.add(batch)
    await session.flush()
    pc = PromoCode(
        batch_id=batch.id,
        code_hash=hash_code(code),
        max_activations=max_activations,
    )
    session.add(pc)
    await session.commit()
    return batch, pc


async def _pro_tier_id(session: AsyncSession) -> uuid.UUID:
    await seed_default_tiers(session)
    await session.commit()
    return (await session.execute(select(Tier).where(Tier.name == PRO))).scalar_one().id


# --- generation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_batch_stores_only_hashes_and_shows_plaintext_once(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, admin = await _user(session, admin=True)
    resp = await client.post(
        "/admin/promo/batches",
        headers=admin,
        json={"name": "launch", "code_type": "upgrade", "size": 500},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warning"] == "plaintext_codes_shown_once"
    assert len(body["codes"]) == 500
    assert len(set(body["codes"])) == 500  # unique

    # DB holds hashes, and each returned plaintext hashes to a stored code.
    count = await session.scalar(select(func.count()).select_from(PromoCode))
    assert count == 500
    stored = set((await session.execute(select(PromoCode.code_hash))).scalars().all())
    assert hash_code(body["codes"][0]) in stored
    assert body["codes"][0] not in stored  # plaintext never stored


@pytest.mark.asyncio
async def test_batch_size_must_be_multiple_of_500(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, admin = await _user(session, admin=True)
    bad = await client.post(
        "/admin/promo/batches",
        headers=admin,
        json={"name": "x", "code_type": "upgrade", "size": 501},
    )
    assert bad.status_code == 422


# --- redemption --------------------------------------------------------------


@pytest.mark.asyncio
async def test_redeem_trial_creates_subscription(
    client: AsyncClient, session: AsyncSession
) -> None:
    tier_id = await _pro_tier_id(session)
    user, headers = await _user(session)
    await _make_code(
        session,
        code="TRIAL-CODE-1",
        code_type=PromoCodeType.trial,
        value=Decimal("14"),
        tier_id=tier_id,
    )

    resp = await client.post("/promo/redeem", headers=headers, json={"code": "TRIAL-CODE-1"})
    assert resp.status_code == 200
    effect = resp.json()["effect"]
    assert effect["type"] == "trial"
    assert effect["status"] == "applied"

    sub = (
        await session.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one()
    assert sub.tier_id == tier_id
    assert sub.expires_at is not None and sub.expires_at > datetime.now(UTC) + timedelta(days=13)


@pytest.mark.asyncio
async def test_redeem_percent_is_pending_no_subscription(
    client: AsyncClient, session: AsyncSession
) -> None:
    user, headers = await _user(session)
    await _make_code(
        session, code="SAVE-30-NOW", code_type=PromoCodeType.percent, value=Decimal("30")
    )

    resp = await client.post("/promo/redeem", headers=headers, json={"code": "SAVE-30-NOW"})
    assert resp.status_code == 200
    effect = resp.json()["effect"]
    assert effect["type"] == "percent"
    assert effect["value"] == "30.00"
    assert effect["status"] == "pending"

    subs = await session.scalar(select(func.count()).select_from(Subscription))
    assert subs == 0
    red = (
        await session.execute(select(PromoRedemption).where(PromoRedemption.user_id == user.id))
    ).scalar_one()
    assert red.status.value == "pending"


@pytest.mark.asyncio
async def test_redeem_unknown_code_404(client: AsyncClient, session: AsyncSession) -> None:
    _, headers = await _user(session)
    resp = await client.post("/promo/redeem", headers=headers, json={"code": "NOPE-NOPE-NOPE"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_redeem_expired_410(client: AsyncClient, session: AsyncSession) -> None:
    _, headers = await _user(session)
    await _make_code(
        session,
        code="OLD-CODE-9999",
        code_type=PromoCodeType.upgrade,
        value=None,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    resp = await client.post("/promo/redeem", headers=headers, json={"code": "OLD-CODE-9999"})
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_redeem_bound_to_other_user_403(client: AsyncClient, session: AsyncSession) -> None:
    owner, _ = await _user(session)
    _, other = await _user(session)
    await _make_code(
        session,
        code="BOUND-CODE-01",
        code_type=PromoCodeType.percent,
        value=Decimal("10"),
        bound_user_id=owner.id,
    )
    resp = await client.post("/promo/redeem", headers=other, json={"code": "BOUND-CODE-01"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_kill_switch_disables_all_codes(client: AsyncClient, session: AsyncSession) -> None:
    _, admin = await _user(session, admin=True)
    _, user = await _user(session)
    batch, _code = await _make_code(
        session, code="KILL-ME-0001", code_type=PromoCodeType.percent, value=Decimal("5")
    )

    kill = await client.post(f"/admin/promo/batches/{batch.id}/kill", headers=admin)
    assert kill.status_code == 200
    assert kill.json()["disabled_codes"] == 1

    resp = await client.post("/promo/redeem", headers=user, json={"code": "KILL-ME-0001"})
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_concurrent_redeem_one_use_code_single_winner(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Two different users race for the same max_activations=1 code.
    _, a = await _user(session)
    _, b = await _user(session)
    await _make_code(
        session,
        code="RACE-CODE-001",
        code_type=PromoCodeType.percent,
        value=Decimal("50"),
        max_activations=1,
    )

    results = await asyncio.gather(
        client.post("/promo/redeem", headers=a, json={"code": "RACE-CODE-001"}),
        client.post("/promo/redeem", headers=b, json={"code": "RACE-CODE-001"}),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]

    code_row = (await session.execute(select(PromoCode))).scalar_one()
    assert code_row.activations_used == 1
    assert code_row.status == PromoCodeStatus.redeemed


@pytest.mark.asyncio
async def test_redeem_rate_limited_returns_429(client: AsyncClient, session: AsyncSession) -> None:
    _, headers = await _user(session)
    # Default limit is 10/hour; the 11th attempt (even invalid) is refused.
    last = None
    for _ in range(11):
        last = await client.post("/promo/redeem", headers=headers, json={"code": "X-X-X"})
    assert last is not None and last.status_code == 429
    assert "Retry-After" in last.headers


# --- admin export + RBAC -----------------------------------------------------


@pytest.mark.asyncio
async def test_export_csv_is_metadata_only(client: AsyncClient, session: AsyncSession) -> None:
    _, admin = await _user(session, admin=True)
    batch, code = await _make_code(
        session, code="EXPORT-CODE-1", code_type=PromoCodeType.upgrade, value=None
    )
    resp = await client.get(f"/admin/promo/batches/{batch.id}/export.csv", headers=admin)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    assert "code_id,status,activations_used,bound_user_id,created_at" in text
    assert str(code.id) in text
    # No plaintext code and no hash column leaks into the export.
    assert "EXPORT-CODE-1" not in text
    assert code.code_hash not in text


@pytest.mark.asyncio
async def test_promo_admin_requires_admin(client: AsyncClient, session: AsyncSession) -> None:
    _, user = await _user(session)  # not an admin
    resp = await client.post(
        "/admin/promo/batches",
        headers=user,
        json={"name": "x", "code_type": "upgrade", "size": 500},
    )
    assert resp.status_code == 403
