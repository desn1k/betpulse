"""Admin user management: RBAC, list (email search + tier filter), manual tier
grant (source=manual subscription), redemptions, and disable/enable with
refresh-token revocation in the same transaction (Phase 12c)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import create_access_token
from app.models.audit_log import AuditLog
from app.models.promo import (
    PromoBatch,
    PromoCodeType,
    PromoRedemption,
    PromoRedemptionStatus,
)
from app.models.refresh_token import RefreshToken
from app.models.tier import Subscription, SubscriptionSource, Tier
from app.models.user import User, UserRole, UserTier
from app.services.tiers import seed_default_tiers
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _admin_headers(session: AsyncSession) -> dict[str, str]:
    admin = User(
        email=f"{uuid.uuid4()}@x.com",
        password_hash="x",
        role=UserRole.admin,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(admin)
    await session.commit()
    return {"Authorization": f"Bearer {create_access_token(subject=str(admin.id), role='admin')}"}


async def _make_user(session: AsyncSession, *, email: str, tier: UserTier = UserTier.free) -> User:
    user = User(email=email, password_hash="x", tier=tier)
    session.add(user)
    await session.flush()
    return user


async def _tier(session: AsyncSession, name: str) -> Tier:
    return (await session.execute(select(Tier).where(Tier.name == name))).scalar_one()


# --- RBAC -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_endpoints_require_admin(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com")
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}

    assert (await client.get("/admin/users", headers=headers)).status_code == 403
    assert (
        await client.post(f"/admin/users/{user.id}/disable", headers=headers)
    ).status_code == 403
    assert (
        await client.post(
            f"/admin/users/{user.id}/tier", headers=headers, json={"tier_id": str(uuid.uuid4())}
        )
    ).status_code == 403


# --- listing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_email_and_effective_tier(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_default_tiers(session)
    alice = await _make_user(session, email="alice@example.com", tier=UserTier.free)
    await _make_user(session, email="bob@example.com", tier=UserTier.free)
    pro = await _tier(session, "pro")
    # Alice gets an active pro subscription → effective tier = pro.
    session.add(Subscription(user_id=alice.id, tier_id=pro.id, source=SubscriptionSource.manual))
    await session.commit()
    headers = await _admin_headers(session)

    # Email search.
    body = (await client.get("/admin/users?email=alice", headers=headers)).json()
    assert body["total"] == 1
    assert body["users"][0]["email"] == "alice@example.com"
    assert body["users"][0]["effective_tier"] == "pro"
    assert body["users"][0]["base_tier"] == "free"

    # Effective-tier filter picks Alice (subscription) but not Bob (base free).
    pro_body = (await client.get("/admin/users?tier=pro", headers=headers)).json()
    emails = {u["email"] for u in pro_body["users"]}
    assert "alice@example.com" in emails
    assert "bob@example.com" not in emails


@pytest.mark.asyncio
async def test_expired_subscription_does_not_count(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_default_tiers(session)
    user = await _make_user(session, email="lapsed@example.com", tier=UserTier.free)
    pro = await _tier(session, "pro")
    session.add(
        Subscription(
            user_id=user.id,
            tier_id=pro.id,
            source=SubscriptionSource.manual,
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),  # long expired
        )
    )
    await session.commit()
    headers = await _admin_headers(session)

    body = (await client.get("/admin/users?email=lapsed", headers=headers)).json()
    assert body["users"][0]["effective_tier"] == "free"  # falls back to base tier


# --- manual tier grant ------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_tier_creates_manual_subscription_and_audits(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_default_tiers(session)
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com", tier=UserTier.free)
    await session.commit()
    expert = await _tier(session, "expert")
    headers = await _admin_headers(session)

    expires = datetime.now(UTC) + timedelta(days=30)
    resp = await client.post(
        f"/admin/users/{user.id}/tier",
        headers=headers,
        json={"tier_id": str(expert.id), "expires_at": expires.isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["effective_tier"] == "expert"

    sub = (
        await session.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one()
    assert sub.source == SubscriptionSource.manual
    assert sub.tier_id == expert.id
    # users.tier itself is untouched.
    fresh = await session.get(User, user.id)
    assert fresh is not None and fresh.tier == UserTier.free

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "user.tier.assign")))
        .scalars()
        .all()
    )
    assert events and events[0].meta["tier"] == "expert"


@pytest.mark.asyncio
async def test_assign_tier_upserts_on_repeat(client: AsyncClient, session: AsyncSession) -> None:
    await seed_default_tiers(session)
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com")
    await session.commit()
    pro = await _tier(session, "pro")
    headers = await _admin_headers(session)

    for _ in range(2):
        resp = await client.post(
            f"/admin/users/{user.id}/tier", headers=headers, json={"tier_id": str(pro.id)}
        )
        assert resp.status_code == 200

    count = (
        (await session.execute(select(Subscription).where(Subscription.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(count) == 1  # uq_subscription_user_tier → upsert, not duplicate


@pytest.mark.asyncio
async def test_assign_tier_unknown_ids_are_404(client: AsyncClient, session: AsyncSession) -> None:
    await seed_default_tiers(session)
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com")
    await session.commit()
    pro = await _tier(session, "pro")
    headers = await _admin_headers(session)

    assert (
        await client.post(
            f"/admin/users/{uuid.uuid4()}/tier", headers=headers, json={"tier_id": str(pro.id)}
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/admin/users/{user.id}/tier", headers=headers, json={"tier_id": str(uuid.uuid4())}
        )
    ).status_code == 404


# --- redemptions ------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_redemptions(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com")
    batch = PromoBatch(name="B", code_type=PromoCodeType.upgrade, size=500, max_activations=1)
    session.add(batch)
    await session.flush()
    session.add(
        PromoRedemption(
            user_id=user.id,
            batch_id=batch.id,
            code_hash="h" * 64,
            code_type=PromoCodeType.upgrade,
            value=None,
            status=PromoRedemptionStatus.applied,
        )
    )
    await session.commit()
    headers = await _admin_headers(session)

    body = (await client.get(f"/admin/users/{user.id}/redemptions", headers=headers)).json()
    assert len(body) == 1
    assert body[0]["code_type"] == "upgrade"
    assert body[0]["status"] == "applied"


# --- disable / enable -------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_revokes_refresh_tokens_same_txn(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com")
    await session.flush()
    for _ in range(3):
        session.add(
            RefreshToken(
                user_id=user.id,
                family_id=uuid.uuid4(),
                token_hash=uuid.uuid4().hex,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.post(f"/admin/users/{user.id}/disable", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert resp.json()["revoked_tokens"] == 3

    uid = user.id
    session.expire_all()
    fresh = await session.get(User, uid)
    assert fresh is not None and fresh.is_active is False
    tokens = (
        (await session.execute(select(RefreshToken).where(RefreshToken.user_id == uid)))
        .scalars()
        .all()
    )
    assert all(t.revoked for t in tokens)

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "user.disable")))
        .scalars()
        .all()
    )
    assert events and events[0].meta["revoked_tokens"] == 3


@pytest.mark.asyncio
async def test_disabled_user_cannot_authenticate(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com", tier=UserTier.expert)
    await session.commit()
    token = create_access_token(subject=str(user.id), role="user")
    user_headers = {"Authorization": f"Bearer {token}"}
    admin_headers = await _admin_headers(session)

    assert (await client.get("/auth/me", headers=user_headers)).status_code == 200

    await client.post(f"/admin/users/{user.id}/disable", headers=admin_headers)

    # A still-valid access token is now rejected because is_active is False.
    resp = await client.get("/auth/me", headers=user_headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_enable_reactivates(client: AsyncClient, session: AsyncSession) -> None:
    user = await _make_user(session, email=f"{uuid.uuid4()}@x.com")
    user.is_active = False
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.post(f"/admin/users/{user.id}/enable", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    uid = user.id
    session.expire_all()
    fresh = await session.get(User, uid)
    assert fresh is not None and fresh.is_active is True
