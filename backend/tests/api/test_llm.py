"""LLM endpoints: tier-gated /matches/{id}/analysis + admin /admin/llm-config."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.redis import get_redis
from app.core.security import create_access_token
from app.models.fixture import Fixture, FixtureStatus
from app.models.llm import LlmConfig
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.models.user import User, UserRole, UserTier
from app.services.llm import analysis as analysis_service
from app.services.llm.analysis import _budget_key
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime.now(UTC)


async def _user_headers(session: AsyncSession, tier: UserTier) -> dict[str, str]:
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


async def _seed_config(session: AsyncSession, **overrides: object) -> None:
    config = LlmConfig(
        singleton="default",
        model="test-model",
        daily_token_budget=100_000,
        cache_ttl_seconds=86_400,
        cost_per_1k_in=Decimal("0.5"),
        cost_per_1k_out=Decimal("1.5"),
        is_enabled=True,
    )
    for field, value in overrides.items():
        setattr(config, field, value)
    session.add(config)
    await session.flush()


async def _seed_fixture(session: AsyncSession, *, rank: int | None) -> uuid.UUID:
    league = League(code=f"L{uuid.uuid4().hex[:4]}", name="League")
    home = Team(name="Home", normalized_name=f"h-{uuid.uuid4().hex[:6]}")
    away = Team(name="Away", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    session.add_all([league, home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=_NOW + timedelta(hours=6),
        status=FixtureStatus.scheduled,
        fixture_llm_rank=rank,
    )
    session.add(fixture)
    await session.flush()
    for method in ("elo", "xg", "consensus", "market"):
        for outcome, prob in (("home", 0.55), ("draw", 0.25), ("away", 0.20)):
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
    await session.flush()
    return fixture.id


def _stub_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(config: LlmConfig, *, system: str, user: str) -> tuple[str, int, int]:
        return "The models agree on a home edge.", 100, 50

    monkeypatch.setattr(analysis_service, "generate_completion", _fake)


# --- tier gating -------------------------------------------------------------


@pytest.mark.asyncio
async def test_guest_is_forbidden(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_config(session)
    fixture_id = await _seed_fixture(session, rank=1)
    await session.commit()

    resp = await client.get(f"/matches/{fixture_id}/analysis")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "llm_requires_upgrade"


@pytest.mark.asyncio
async def test_free_sees_only_match_of_the_day(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_completion(monkeypatch)
    await _seed_config(session)
    mod = await _seed_fixture(session, rank=1)
    top5 = await _seed_fixture(session, rank=3)
    unranked = await _seed_fixture(session, rank=None)
    await session.commit()
    headers = await _user_headers(session, UserTier.free)

    ok = await client.get(f"/matches/{mod}/analysis", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["is_match_of_the_day"] is True

    forbidden = await client.get(f"/matches/{top5}/analysis", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["tier_required"] == "pro"

    expert_only = await client.get(f"/matches/{unranked}/analysis", headers=headers)
    assert expert_only.status_code == 403
    assert expert_only.json()["detail"]["tier_required"] == "expert"


@pytest.mark.asyncio
async def test_pro_sees_top5_not_unranked(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_completion(monkeypatch)
    await _seed_config(session)
    top5 = await _seed_fixture(session, rank=5)
    unranked = await _seed_fixture(session, rank=None)
    await session.commit()
    headers = await _user_headers(session, UserTier.pro)

    assert (await client.get(f"/matches/{top5}/analysis", headers=headers)).status_code == 200
    forbidden = await client.get(f"/matches/{unranked}/analysis", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["tier_required"] == "expert"


@pytest.mark.asyncio
async def test_expert_sees_any_fixture(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_completion(monkeypatch)
    await _seed_config(session)
    unranked = await _seed_fixture(session, rank=None)
    await session.commit()
    headers = await _user_headers(session, UserTier.expert)

    resp = await client.get(f"/matches/{unranked}/analysis", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["is_match_of_the_day"] is False


# --- response contract -------------------------------------------------------


@pytest.mark.asyncio
async def test_response_has_top_level_disclaimer_flag(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_completion(monkeypatch)
    await _seed_config(session)
    fixture_id = await _seed_fixture(session, rank=1)
    await session.commit()
    headers = await _user_headers(session, UserTier.free)

    body = (await client.get(f"/matches/{fixture_id}/analysis", headers=headers)).json()
    # Top-level, not buried in content — the frontend renders it unconditionally.
    assert body["not_a_probability_source"] is True
    assert body["content"]
    assert body["cached"] is False


@pytest.mark.asyncio
async def test_budget_exhausted_returns_reset_time(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_completion(monkeypatch)
    await _seed_config(session, daily_token_budget=10)
    fixture_id = await _seed_fixture(session, rank=1)
    await session.commit()
    headers = await _user_headers(session, UserTier.free)
    redis = get_redis()
    await redis.set(_budget_key(datetime.now(UTC)), 10)

    resp = await client.get(f"/matches/{fixture_id}/analysis", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "budget_exhausted"
    assert body["resets_at"] is not None
    assert body["content"] is None


@pytest.mark.asyncio
async def test_unknown_fixture_is_404(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_config(session)
    await session.commit()
    headers = await _user_headers(session, UserTier.expert)
    resp = await client.get(f"/matches/{uuid.uuid4()}/analysis", headers=headers)
    assert resp.status_code == 404


# --- admin config ------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reads_masked_config(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_config(session, encrypted_key="enc", key_suffix="1234")
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.get("/admin/llm-config", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["key_masked"] == "••••1234"
    assert "encrypted_key" not in body
    assert "api_key" not in body


@pytest.mark.asyncio
async def test_admin_patch_stores_key_masked_and_audited(
    client: AsyncClient, session: AsyncSession
) -> None:
    from app.models.audit_log import AuditLog

    headers = await _admin_headers(session)
    resp = await client.patch(
        "/admin/llm-config",
        headers=headers,
        json={"model": "gpt-x", "api_key": "sk-secret-abcd", "is_enabled": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "gpt-x"
    assert body["key_masked"] == "••••abcd"  # last 4 only
    # The raw key is never echoed back anywhere in the payload.
    assert "sk-secret-abcd" not in resp.text

    # Stored encrypted, not in plaintext.
    config = (
        await session.execute(select(LlmConfig).where(LlmConfig.singleton == "default"))
    ).scalar_one()
    assert config.encrypted_key is not None
    assert "sk-secret-abcd" not in config.encrypted_key
    assert config.key_suffix == "abcd"

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "llm_config.update")))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].target == "llm_config"
    assert "api_key" in events[0].meta["fields"]


@pytest.mark.asyncio
async def test_llm_config_requires_admin(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _user_headers(session, UserTier.expert)
    assert (await client.get("/admin/llm-config", headers=headers)).status_code == 403
    assert (
        await client.patch("/admin/llm-config", headers=headers, json={"model": "x"})
    ).status_code == 403
