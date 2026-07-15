"""Tier enforcement on the match endpoints + admin tier management (spec §7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.security import create_access_token
from app.models.fixture import Fixture, FixtureStatus
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.models.tier import Subscription, SubscriptionSource, Tier
from app.models.user import User, UserRole, UserTier
from app.services.tiers import PRO, seed_default_tiers
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_METHOD_HOME = {
    "elo": (0.50, 0.30, 0.20),
    "dixon_coles": (0.48, 0.30, 0.22),
    "lightgbm": (0.49, 0.31, 0.20),
    "market": (0.40, 0.30, 0.30),
    "consensus": (0.52, 0.28, 0.20),
}


async def _seed_match(session: AsyncSession) -> Fixture:
    session.add_all(
        [
            ModelRegistry(
                method=m,
                version="v1",
                status=ModelStatus.champion if m == "lightgbm" else ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("20"),
                accuracy_pct=Decimal("55.0"),
                sample_count=400,
            )
            for m in _METHOD_HOME
        ]
    )
    league = League(code="EPL", name="Premier League")
    session.add(league)
    home = Team(name="A", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    away = Team(name="B", normalized_name=f"b-{uuid.uuid4().hex[:6]}")
    session.add_all([home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(UTC) + timedelta(hours=6),
        status=FixtureStatus.scheduled,
    )
    session.add(fixture)
    await session.flush()
    for method, (h, d, a) in _METHOD_HOME.items():
        for outcome, prob in (("home", h), ("draw", d), ("away", a)):
            session.add(
                Prediction(
                    fixture_id=fixture.id,
                    method=method,
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal(str(prob)),
                    model_version="v1",
                )
            )
    await session.commit()
    return fixture


async def _tier_headers(session: AsyncSession, tier: UserTier) -> dict[str, str]:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=tier)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


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
    token = create_access_token(subject=str(admin.id), role="admin")
    return {"Authorization": f"Bearer {token}"}


# --- method-bar gating per tier ---------------------------------------------


@pytest.mark.asyncio
async def test_guest_gets_no_method_bars_blurred_consensus(
    client: AsyncClient, session: AsyncSession
) -> None:
    fixture = await _seed_match(session)
    body = (await client.get(f"/matches/{fixture.id}")).json()
    assert body["methods"] == []
    assert body["flags"]["methods"] == "blurred_consensus"
    # Consensus + aggregate signals are still present.
    assert body["consensus"] is not None
    assert body["model_agreement_pct"] is not None


@pytest.mark.asyncio
async def test_free_gets_consensus_only(client: AsyncClient, session: AsyncSession) -> None:
    fixture = await _seed_match(session)
    headers = await _tier_headers(session, UserTier.free)
    body = (await client.get(f"/matches/{fixture.id}", headers=headers)).json()
    assert body["methods"] == []
    assert body["flags"]["methods"] == "consensus"


@pytest.mark.asyncio
async def test_pro_sees_bars_without_weights(client: AsyncClient, session: AsyncSession) -> None:
    fixture = await _seed_match(session)
    headers = await _tier_headers(session, UserTier.pro)
    body = (await client.get(f"/matches/{fixture.id}", headers=headers)).json()
    assert len(body["methods"]) > 0
    assert body["flags"]["methods"] == "all"
    assert all(m["weight"] is None for m in body["methods"])


@pytest.mark.asyncio
async def test_expert_sees_bars_with_weights(client: AsyncClient, session: AsyncSession) -> None:
    fixture = await _seed_match(session)
    headers = await _tier_headers(session, UserTier.expert)
    body = (await client.get(f"/matches/{fixture.id}", headers=headers)).json()
    assert body["flags"]["methods"] == "all_weights"
    assert all(m["weight"] == pytest.approx(20.0) for m in body["methods"])


# --- subscription overrides --------------------------------------------------


@pytest.mark.asyncio
async def test_active_subscription_overrides_base_tier(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_default_tiers(session)
    fixture = await _seed_match(session)
    # Base tier free, but an active manual pro subscription lifts them to pro.
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.free)
    session.add(user)
    await session.flush()
    pro_tier = (await session.execute(select(Tier).where(Tier.name == PRO))).scalar_one()
    session.add(
        Subscription(user_id=user.id, tier_id=pro_tier.id, source=SubscriptionSource.manual)
    )
    await session.commit()
    token = create_access_token(subject=str(user.id), role="user")
    headers = {"Authorization": f"Bearer {token}"}

    body = (await client.get(f"/matches/{fixture.id}", headers=headers)).json()
    assert body["flags"]["methods"] == "all"
    assert len(body["methods"]) > 0


@pytest.mark.asyncio
async def test_expired_subscription_is_ignored(client: AsyncClient, session: AsyncSession) -> None:
    await seed_default_tiers(session)
    fixture = await _seed_match(session)
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.free)
    session.add(user)
    await session.flush()
    pro_tier = (await session.execute(select(Tier).where(Tier.name == PRO))).scalar_one()
    session.add(
        Subscription(
            user_id=user.id,
            tier_id=pro_tier.id,
            source=SubscriptionSource.manual,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await session.commit()
    token = create_access_token(subject=str(user.id), role="user")
    body = (
        await client.get(f"/matches/{fixture.id}", headers={"Authorization": f"Bearer {token}"})
    ).json()
    # Lapsed subscription → falls back to the free base tier.
    assert body["flags"]["methods"] == "consensus"


# --- matches/day limit -------------------------------------------------------


@pytest.mark.asyncio
async def test_guest_daily_limit_then_403(client: AsyncClient, session: AsyncSession) -> None:
    fixture = await _seed_match(session)
    url = f"/matches/{fixture.id}"
    # Guest budget is 3/day (per IP). Fourth view is refused.
    for _ in range(3):
        assert (await client.get(url)).status_code == 200
    blocked = await client.get(url)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["tier_required"] == "free"


@pytest.mark.asyncio
async def test_list_reports_matches_remaining(client: AsyncClient, session: AsyncSession) -> None:
    fixture = await _seed_match(session)
    # Guest starts with 3; after two detail views the list reports 1 remaining.
    first = (await client.get("/matches")).json()
    assert first["matches_remaining"] == 3
    await client.get(f"/matches/{fixture.id}")
    await client.get(f"/matches/{fixture.id}")
    after = (await client.get("/matches")).json()
    assert after["matches_remaining"] == 1


# --- admin tier management ---------------------------------------------------


@pytest.mark.asyncio
async def test_admin_lists_and_edits_tiers_reflected_immediately(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_default_tiers(session)
    fixture = await _seed_match(session)
    admin = await _admin_headers(session)

    listed = await client.get("/admin/tiers", headers=admin)
    assert listed.status_code == 200
    names = {t["name"] for t in listed.json()}
    assert {"guest", "free", "pro", "expert"} <= names
    free = next(t for t in listed.json() if t["name"] == "free")

    # Drop the free daily limit to 1 and confirm it takes effect on the next
    # request (cache invalidated on PATCH, not after the 60s TTL).
    patch = await client.patch(
        f"/admin/tiers/{free['id']}",
        headers=admin,
        json={"limits": {"matches_per_day": 1, "pushes_per_day": 1, "backtester_runs_per_day": 3}},
    )
    assert patch.status_code == 200
    assert patch.json()["limits"]["matches_per_day"] == 1

    free_headers = await _tier_headers(session, UserTier.free)
    url = f"/matches/{fixture.id}"
    assert (await client.get(url, headers=free_headers)).status_code == 200
    blocked = await client.get(url, headers=free_headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["tier_required"] == "pro"


@pytest.mark.asyncio
async def test_admin_tier_edit_is_audited(client: AsyncClient, session: AsyncSession) -> None:
    from app.models.audit_log import AuditLog

    await seed_default_tiers(session)
    admin = await _admin_headers(session)
    pro = (await session.execute(select(Tier).where(Tier.name == PRO))).scalar_one()

    await client.patch(f"/admin/tiers/{pro.id}", headers=admin, json={"price": "14.99"})

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "tier.update")))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].target == "tier:pro"
    assert "price" in events[0].meta["fields"]


@pytest.mark.asyncio
async def test_tier_endpoints_require_admin(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _tier_headers(session, UserTier.pro)  # a normal user
    assert (await client.get("/admin/tiers", headers=headers)).status_code == 403
