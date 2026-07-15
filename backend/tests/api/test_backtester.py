"""Backtester API: run, tier limits, save/export gating, CSV export."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.security import create_access_token
from app.models.backtester import BacktestFeature
from app.models.fixture import Fixture, FixtureStatus
from app.models.reference import League, Team
from app.models.user import User, UserTier
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _headers(session: AsyncSession, tier: UserTier) -> dict[str, str]:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=tier)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role="user")
    return {"Authorization": f"Bearer {token}"}


async def _seed(session: AsyncSession, count: int = 3) -> None:
    league = League(code="EPL", name="EPL")
    home = Team(name="Home FC", normalized_name=f"h-{uuid.uuid4().hex[:6]}")
    away = Team(name="Away FC", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    session.add_all([league, home, away])
    await session.flush()
    for i in range(count):
        ko = datetime(2023, 8, 1, tzinfo=UTC) + timedelta(days=i)
        fx = Fixture(
            league_id=league.id,
            season="2023-2024",
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=ko,
            status=FixtureStatus.finished,
            ft_home=2,
            ft_away=0,
        )
        session.add(fx)
        await session.flush()
        session.add(
            BacktestFeature(
                fixture_id=fx.id,
                league_id=league.id,
                league_code="EPL",
                season="2023-2024",
                kickoff_at=ko,
                home_team_id=home.id,
                away_team_id=away.id,
                home_team=home.name,
                away_team=away.name,
                ft_home=2,
                ft_away=0,
                total_goals=2,
                elo_home=1500,
                elo_away=1500,
                elo_diff=0,
                rolling_xg_home=1.4,
                rolling_xg_away=1.3,
                avg_total=2.7,
                rest_days_home=7,
                rest_days_away=7,
                form_home=1.5,
                form_away=1.5,
                odds_home=Decimal("2.00"),
                odds_draw=Decimal("3.40"),
                odds_away=Decimal("3.60"),
                odds_over=Decimal("1.90"),
                odds_under=Decimal("1.95"),
            )
        )
    await session.commit()


_RUN = {"bet_type": "1x2", "pick": "home", "filters": {"league": "EPL"}}


@pytest.mark.asyncio
async def test_run_returns_metrics_and_disclaimer(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed(session, 3)
    headers = await _headers(session, UserTier.free)
    resp = await client.post("/backtester/run", headers=headers, json=_RUN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_count"] == 3
    assert body["roi_disclaimer"] is True
    assert body["small_sample_warning"] is True
    assert body["win_rate_ci"]["confidence"] == 0.95


@pytest.mark.asyncio
async def test_run_requires_auth(client: AsyncClient, session: AsyncSession) -> None:
    assert (await client.post("/backtester/run", json=_RUN)).status_code == 401


@pytest.mark.asyncio
async def test_free_daily_run_limit_then_403(client: AsyncClient, session: AsyncSession) -> None:
    await _seed(session, 1)
    headers = await _headers(session, UserTier.free)  # 3 runs/day
    for _ in range(3):
        assert (await client.post("/backtester/run", headers=headers, json=_RUN)).status_code == 200
    blocked = await client.post("/backtester/run", headers=headers, json=_RUN)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["tier_required"] == "pro"


@pytest.mark.asyncio
async def test_walk_forward_query_param(client: AsyncClient, session: AsyncSession) -> None:
    await _seed(session, 2)
    headers = await _headers(session, UserTier.pro)
    resp = await client.post("/backtester/run?walk_forward=true", headers=headers, json=_RUN)
    assert resp.status_code == 200
    assert resp.json()["walk_forward"] is True


@pytest.mark.asyncio
async def test_numeric_filter_rejects_sql_string_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _headers(session, UserTier.free)
    resp = await client.post(
        "/backtester/run",
        headers=headers,
        json={"bet_type": "1x2", "pick": "home", "filters": {"odds_min": "1.5 OR 1=1"}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_save_gated_to_expert(client: AsyncClient, session: AsyncSession) -> None:
    free = await _headers(session, UserTier.free)
    payload = {"name": "my strat", "bet_type": "1x2", "pick": "home", "filters": {"league": "EPL"}}
    assert (
        await client.post("/backtester/strategies", headers=free, json=payload)
    ).status_code == 403

    expert = await _headers(session, UserTier.expert)
    created = await client.post("/backtester/strategies", headers=expert, json=payload)
    assert created.status_code == 201
    assert created.json()["pick"] == "home"


@pytest.mark.asyncio
async def test_export_csv_columns_no_uuids(client: AsyncClient, session: AsyncSession) -> None:
    await _seed(session, 2)
    expert = await _headers(session, UserTier.expert)
    payload = {"name": "expo", "bet_type": "1x2", "pick": "home", "filters": {"league": "EPL"}}
    strat = (await client.post("/backtester/strategies", headers=expert, json=payload)).json()

    resp = await client.get(f"/backtester/strategies/{strat['id']}/export.csv", headers=expert)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0] == (
        "date,home_team,away_team,league,season,bet_type,pick,odds,outcome,pnl,cumulative_pnl"
    )
    assert len(lines) == 3  # header + 2 bets
    # No internal UUIDs anywhere in the export.
    assert strat["id"] not in resp.text
    assert "Home FC" in resp.text and "win" in resp.text


@pytest.mark.asyncio
async def test_export_gated_to_expert(client: AsyncClient, session: AsyncSession) -> None:
    # A free user cannot even reach export (403 before any strategy lookup).
    free = await _headers(session, UserTier.free)
    resp = await client.get(f"/backtester/strategies/{uuid.uuid4()}/export.csv", headers=free)
    assert resp.status_code == 403
