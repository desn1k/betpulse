"""Backtester filter validation (M-03): decimal odds are > 1.0 and bounded, no
NaN/inf, and every min/max pair is ordered. Invalid filters are rejected with
422 before any quota is spent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.core.redis import get_redis
from app.core.security import create_access_token
from app.models.backtester import Strategy
from app.models.user import User, UserTier
from app.schemas.backtester import ODDS_UPPER_BOUND, StrategyFilter
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.parametrize(
    "filters",
    [
        {"odds_min": 1.0},  # even money minus the stake: not a real decimal price
        {"odds_max": 1.0},
        {"odds_min": 0.5},
        {"odds_min": -2},
        {"odds_max": ODDS_UPPER_BOUND + 1},
        {"odds_min": 3.0, "odds_max": 2.0},
        {"odds_min": float("inf")},
        {"odds_max": float("nan")},
        {"elo_diff_min": 50, "elo_diff_max": -50},
        {"avg_total_min": 3.5, "avg_total_max": 2.5},
        {"elo_diff_min": float("-inf")},
    ],
)
def test_invalid_odds_ranges_are_rejected(filters: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StrategyFilter(**filters)


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"odds_min": 1.01},
        {"odds_max": ODDS_UPPER_BOUND},
        {"odds_min": 2.0, "odds_max": 2.0},
        {"odds_min": 1.5, "odds_max": 3.5},
        {"elo_diff_min": -50, "elo_diff_max": -50},
        {"elo_diff_min": -100, "elo_diff_max": 100},
        {"avg_total_min": 2.5, "avg_total_max": 3.5},
        {"avg_total_max": 1.0},
    ],
)
def test_valid_odds_ranges_are_accepted(filters: dict[str, Any]) -> None:
    StrategyFilter(**filters)


@pytest.mark.asyncio
async def test_run_rejects_inverted_odds_range_with_422_without_spending_quota(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.free)
    session.add(user)
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}

    body = {"bet_type": "1x2", "pick": "home", "filters": {"odds_min": 3.0, "odds_max": 2.0}}
    resp = await client.post("/backtester/run", headers=headers, json=body)

    assert resp.status_code == 422
    key = f"limits:backtester:{user.id}:{datetime.now(UTC):%Y-%m-%d}"
    assert await get_redis().get(key) is None


@pytest.mark.asyncio
async def test_run_rejects_inverted_elo_range_with_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.free)
    session.add(user)
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}
    body = {"bet_type": "1x2", "pick": "home", "filters": {"elo_diff_min": 1, "elo_diff_max": 0}}
    resp = await client.post("/backtester/run", headers=headers, json=body)
    assert resp.status_code == 422
    assert "elo_diff_min must be less than or equal to elo_diff_max" in resp.text


@pytest.mark.asyncio
async def test_export_of_strategy_saved_with_now_invalid_filters_is_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A strategy saved before the rules were tightened must not 500 on export."""
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.expert)
    session.add(user)
    await session.flush()
    legacy = Strategy(
        user_id=user.id, name="legacy", bet_type="1x2", pick="home", filters={"odds_min": 1.0}
    )
    session.add(legacy)
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}

    resp = await client.get(f"/backtester/strategies/{legacy.id}/export.csv", headers=headers)

    assert resp.status_code == 422
    assert "save the strategy again" in resp.json()["detail"]
