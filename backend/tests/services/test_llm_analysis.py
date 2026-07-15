"""LLM analysis service: cache, daily budget, cost, generation (spec §8).

``generate_completion`` is the only network call; every test monkeypatches it so
no live key is needed. We assert the cache/budget/cost bookkeeping around it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.redis import get_redis
from app.models.fixture import Fixture, FixtureStatus
from app.models.llm import LlmAnalysis, LlmConfig
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.services.llm import analysis as analysis_service
from app.services.llm.analysis import _budget_key, _cost, get_or_create_analysis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


async def _seed_config(
    session: AsyncSession,
    *,
    is_enabled: bool = True,
    daily_token_budget: int = 100_000,
    cache_ttl_seconds: int = 86_400,
) -> LlmConfig:
    config = LlmConfig(
        singleton="default",
        base_url="",
        model="test-model",
        max_tokens=600,
        daily_token_budget=daily_token_budget,
        cache_ttl_seconds=cache_ttl_seconds,
        cost_per_1k_in=Decimal("0.5"),
        cost_per_1k_out=Decimal("1.5"),
        is_enabled=is_enabled,
    )
    session.add(config)
    await session.flush()
    return config


async def _seed_fixture(session: AsyncSession, *, with_consensus: bool = True) -> uuid.UUID:
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
    )
    session.add(fixture)
    await session.flush()
    methods = ("elo", "xg", "consensus", "market") if with_consensus else ("elo", "xg")
    for method in methods:
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


def _stub_completion(
    monkeypatch: pytest.MonkeyPatch, content: str = "Because the models agree."
) -> dict[str, int]:
    calls = {"n": 0}

    async def _fake(config: LlmConfig, *, system: str, user: str) -> tuple[str, int, int]:
        calls["n"] += 1
        return content, 100, 50

    monkeypatch.setattr(analysis_service, "generate_completion", _fake)
    return calls


@pytest.mark.asyncio
async def test_generates_persists_and_charges_budget(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_config(session)
    fixture_id = await _seed_fixture(session)
    await session.commit()
    calls = _stub_completion(monkeypatch)
    redis = get_redis()

    result = await get_or_create_analysis(
        session, redis, fixture_id=fixture_id, language="en", now=_NOW
    )
    await session.commit()

    assert result.status == "ok"
    assert result.cached is False
    assert result.content == "Because the models agree."
    assert result.not_a_probability_source is True
    assert result.tokens_in == 100 and result.tokens_out == 50
    assert result.cost == pytest.approx(0.5 * 0.1 + 1.5 * 0.05)  # 0.05 + 0.075
    assert calls["n"] == 1

    row = (
        await session.execute(select(LlmAnalysis).where(LlmAnalysis.fixture_id == fixture_id))
    ).scalar_one()
    assert row.model == "test-model"
    assert float(row.cost) == pytest.approx(0.125)
    # Budget charged with the total tokens, TTL set.
    assert int(await redis.get(_budget_key(_NOW))) == 150
    assert await redis.ttl(_budget_key(_NOW)) > 0


@pytest.mark.asyncio
async def test_cache_hit_skips_second_call(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_config(session)
    fixture_id = await _seed_fixture(session)
    await session.commit()
    calls = _stub_completion(monkeypatch)
    redis = get_redis()

    first = await get_or_create_analysis(
        session, redis, fixture_id=fixture_id, language="en", now=_NOW
    )
    await session.commit()
    second = await get_or_create_analysis(
        session, redis, fixture_id=fixture_id, language="en", now=_NOW + timedelta(seconds=30)
    )
    await session.commit()

    assert first.cached is False
    assert second.cached is True
    assert second.content == first.content
    assert calls["n"] == 1  # served from cache — no new generation
    count = (
        await session.execute(
            select(func.count())
            .select_from(LlmAnalysis)
            .where(LlmAnalysis.fixture_id == fixture_id)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_stale_cache_regenerates(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_config(session, cache_ttl_seconds=100)
    fixture_id = await _seed_fixture(session)
    await session.commit()
    calls = _stub_completion(monkeypatch)
    redis = get_redis()

    await get_or_create_analysis(session, redis, fixture_id=fixture_id, language="en", now=_NOW)
    await session.commit()
    # Past the TTL (same UTC day) → regenerate, do not serve stale.
    regen = await get_or_create_analysis(
        session, redis, fixture_id=fixture_id, language="en", now=_NOW + timedelta(seconds=200)
    )
    await session.commit()

    assert regen.cached is False
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_budget_exhausted_hard_stop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_config(session, daily_token_budget=100)
    fixture_id = await _seed_fixture(session)
    await session.commit()
    calls = _stub_completion(monkeypatch)
    redis = get_redis()
    await redis.set(_budget_key(_NOW), 100)  # already at budget

    result = await get_or_create_analysis(
        session, redis, fixture_id=fixture_id, language="en", now=_NOW
    )

    assert result.status == "budget_exhausted"
    assert result.content is None
    assert result.resets_at == "2026-07-16T00:00:00+00:00"
    assert calls["n"] == 0  # never started a generation
    count = (
        await session.execute(
            select(func.count())
            .select_from(LlmAnalysis)
            .where(LlmAnalysis.fixture_id == fixture_id)
        )
    ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_disabled_config_returns_disabled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_config(session, is_enabled=False)
    fixture_id = await _seed_fixture(session)
    await session.commit()
    calls = _stub_completion(monkeypatch)

    result = await get_or_create_analysis(
        session, get_redis(), fixture_id=fixture_id, language="en", now=_NOW
    )

    assert result.status == "disabled"
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_no_consensus_returns_no_data(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_config(session)
    fixture_id = await _seed_fixture(session, with_consensus=False)
    await session.commit()
    calls = _stub_completion(monkeypatch)

    result = await get_or_create_analysis(
        session, get_redis(), fixture_id=fixture_id, language="en", now=_NOW
    )

    assert result.status == "no_data"
    assert calls["n"] == 0


def test_cost_from_rates() -> None:
    config = LlmConfig(cost_per_1k_in=Decimal("0.5"), cost_per_1k_out=Decimal("1.5"))
    assert _cost(config, 2000, 1000) == pytest.approx(0.5 * 2 + 1.5 * 1)
