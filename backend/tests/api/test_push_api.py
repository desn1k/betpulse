"""Push endpoints: tier-gated subscribe/follow, VAPID key, latest-swing,
Telegram deep-link + webhook (Phase 11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import get_settings_dep
from app.core.security import create_access_token
from app.main import app
from app.models.fixture import Fixture, FixtureStatus
from app.models.live import LiveUpdate, PushChannel, PushSubscription
from app.models.reference import League, Team
from app.models.user import User, UserTier
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

WEBHOOK_SECRET = "hook-secret"  # noqa: S105


async def _headers(session: AsyncSession, tier: UserTier) -> dict[str, str]:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=tier)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _make_fixture(session: AsyncSession) -> uuid.UUID:
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
        kickoff_at=datetime.now(UTC) + timedelta(hours=2),
        status=FixtureStatus.live,
    )
    session.add(fixture)
    await session.commit()
    return fixture.id


def _override_settings(**overrides: object) -> None:
    from app.core.config import get_settings

    app.dependency_overrides[get_settings_dep] = lambda: get_settings().model_copy(update=overrides)


def _clear_settings_override() -> None:
    app.dependency_overrides.pop(get_settings_dep, None)


# --- tier gating -------------------------------------------------------------


@pytest.mark.asyncio
async def test_free_cannot_subscribe(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _headers(session, UserTier.free)
    resp = await client.post(
        "/live/push/subscribe",
        headers=headers,
        json={"channel": "webpush", "endpoint": "https://push.example/x", "keys": {}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "push_requires_upgrade"


@pytest.mark.asyncio
async def test_pro_can_subscribe(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _headers(session, UserTier.pro)
    resp = await client.post(
        "/live/push/subscribe",
        headers=headers,
        json={"channel": "webpush", "endpoint": "https://push.example/x", "keys": {"auth": "a"}},
    )
    assert resp.status_code == 201
    assert resp.json()["channel"] == "webpush"


@pytest.mark.asyncio
async def test_follow_requires_pro(client: AsyncClient, session: AsyncSession) -> None:
    fixture_id = await _make_fixture(session)
    headers = await _headers(session, UserTier.free)
    resp = await client.put(f"/live/push/follow/{fixture_id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_pro_follow_list_and_unfollow(client: AsyncClient, session: AsyncSession) -> None:
    fixture_id = await _make_fixture(session)
    headers = await _headers(session, UserTier.pro)

    followed = await client.put(f"/live/push/follow/{fixture_id}", headers=headers)
    assert followed.status_code == 200
    assert followed.json() == {"fixture_id": str(fixture_id), "following": True}

    listed = await client.get("/live/push/follows", headers=headers)
    assert listed.json()["fixture_ids"] == [str(fixture_id)]

    unfollowed = await client.delete(f"/live/push/follow/{fixture_id}", headers=headers)
    assert unfollowed.json()["following"] is False
    assert (await client.get("/live/push/follows", headers=headers)).json()["fixture_ids"] == []


@pytest.mark.asyncio
async def test_follow_unknown_fixture_404(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _headers(session, UserTier.pro)
    resp = await client.put(f"/live/push/follow/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


# --- public read: VAPID key + latest swing ----------------------------------


@pytest.mark.asyncio
async def test_vapid_public_key_is_public(client: AsyncClient) -> None:
    _override_settings(webpush_vapid_public_key="BPUBLIC")
    try:
        resp = await client.get("/push/vapid-public-key")
    finally:
        _clear_settings_override()
    assert resp.status_code == 200
    assert resp.json()["public_key"] == "BPUBLIC"


@pytest.mark.asyncio
async def test_latest_swing_returns_snapshot(client: AsyncClient, session: AsyncSession) -> None:
    fixture_id = await _make_fixture(session)
    session.add(
        LiveUpdate(
            fixture_id=fixture_id,
            minute=57,
            home_score=1,
            away_score=0,
            payload={"probs": {"1x2": {"home": 0.6, "draw": 0.25, "away": 0.15}}},
        )
    )
    await session.commit()

    resp = await client.get(f"/live/push/latest/{fixture_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["minute"] == 57 and body["home_score"] == 1
    assert body["probs"]["1x2"]["home"] == 0.6
    assert body["home_team"] == "Home"


@pytest.mark.asyncio
async def test_latest_swing_404_when_no_update(client: AsyncClient, session: AsyncSession) -> None:
    fixture_id = await _make_fixture(session)
    resp = await client.get(f"/live/push/latest/{fixture_id}")
    assert resp.status_code == 404


# --- Telegram deep-link + webhook -------------------------------------------


@pytest.mark.asyncio
async def test_telegram_link_then_webhook_connects(
    client: AsyncClient, session: AsyncSession
) -> None:
    _override_settings(telegram_webhook_secret=WEBHOOK_SECRET, telegram_bot_username="mybot")
    try:
        headers = await _headers(session, UserTier.pro)
        link = await client.post("/push/telegram/link", headers=headers)
        assert link.status_code == 200
        url = link.json()["url"]
        assert url.startswith("https://t.me/mybot?start=")
        token = url.split("start=")[1]

        # Telegram calls the webhook with the deep-link token and the chat id.
        hook = await client.post(
            "/push/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
            json={"message": {"text": f"/start {token}", "chat": {"id": 987654}}},
        )
        assert hook.status_code == 200

        subs = await client.get("/push/subscriptions", headers=headers)
        assert subs.json()["telegram_connected"] is True
    finally:
        _clear_settings_override()

    row = (
        await session.execute(
            select(PushSubscription).where(PushSubscription.channel == PushChannel.telegram)
        )
    ).scalar_one()
    assert row.endpoint == "987654"


@pytest.mark.asyncio
async def test_webhook_bad_secret_is_200_and_noop(
    client: AsyncClient, session: AsyncSession
) -> None:
    _override_settings(telegram_webhook_secret=WEBHOOK_SECRET, telegram_bot_username="mybot")
    try:
        headers = await _headers(session, UserTier.pro)
        link = await client.post("/push/telegram/link", headers=headers)
        token = link.json()["url"].split("start=")[1]

        # Wrong secret → still 200 (so Telegram won't retry) but no side effect.
        hook = await client.post(
            "/push/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"message": {"text": f"/start {token}", "chat": {"id": 5}}},
        )
        assert hook.status_code == 200
    finally:
        _clear_settings_override()

    count = (
        await session.execute(
            select(PushSubscription).where(PushSubscription.channel == PushChannel.telegram)
        )
    ).all()
    assert count == []


@pytest.mark.asyncio
async def test_webhook_missing_secret_is_200_and_noop(client: AsyncClient) -> None:
    _override_settings(telegram_webhook_secret=WEBHOOK_SECRET, telegram_bot_username="mybot")
    try:
        hook = await client.post(
            "/push/telegram/webhook",
            json={"message": {"text": "/start whatever", "chat": {"id": 5}}},
        )
        assert hook.status_code == 200
    finally:
        _clear_settings_override()


@pytest.mark.asyncio
async def test_disconnect_telegram(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _headers(session, UserTier.pro)
    # Exactly one user exists in this truncated-per-test DB.
    user = (await session.execute(select(User))).scalar_one()
    session.add(PushSubscription(user_id=user.id, channel=PushChannel.telegram, endpoint="42"))
    await session.commit()

    resp = await client.delete("/push/telegram", headers=headers)
    assert resp.status_code == 204
    subs = await client.get("/push/subscriptions", headers=headers)
    assert subs.json()["telegram_connected"] is False
