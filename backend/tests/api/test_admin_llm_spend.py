"""Admin LLM spend dashboard: RBAC, deterministic UTC-day aggregation, top
fixtures by cost, and the ``days`` range validation (Phase 12c).

Spend is read from the ``llm_generations`` journal (ER-H-02): one row per LLM
call, with the cost computed when it was written. Journal rows are seeded with
**explicit UTC timestamps**, never ``datetime.now()``, so the daily buckets are
deterministic regardless of when the suite runs.
"""

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
from app.services.llm.analysis import get_or_create_analysis
from httpx import AsyncClient
from sqlalchemy import select, text
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


async def _user_headers(session: AsyncSession) -> dict[str, str]:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.expert)
    session.add(user)
    await session.commit()
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}


async def _seed_fixture(session: AsyncSession, *, home: str, away: str) -> uuid.UUID:
    league = League(code=f"L{uuid.uuid4().hex[:4]}", name="Premier League")
    h = Team(name=home, normalized_name=f"h-{uuid.uuid4().hex[:6]}")
    a = Team(name=away, normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    session.add_all([league, h, a])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=h.id,
        away_team_id=a.id,
        kickoff_at=datetime(2026, 1, 2, 18, 0, tzinfo=UTC),
        status=FixtureStatus.scheduled,
    )
    session.add(fixture)
    await session.flush()
    return fixture.id


async def _seed_generation(
    session: AsyncSession,
    *,
    fixture_id: uuid.UUID,
    created_at: datetime,
    cost: str,
    tokens_in: int = 100,
    tokens_out: int = 50,
) -> None:
    await session.execute(
        text(
            "INSERT INTO llm_generations (id, fixture_id, model, language, tokens_in, "
            "tokens_out, cost, created_at) VALUES (:id, :fid, 'm', 'en', :tin, :tout, "
            ":cost, :at)"
        ),
        {
            "id": uuid.uuid4(),
            "fid": fixture_id,
            "tin": tokens_in,
            "tout": tokens_out,
            "cost": Decimal(cost),
            "at": created_at,
        },
    )


# --- RBAC + validation ------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_requires_admin(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _user_headers(session)
    assert (await client.get("/admin/llm/spend", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_days_out_of_range_is_422(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _admin_headers(session)
    assert (await client.get("/admin/llm/spend?days=0", headers=headers)).status_code == 422
    assert (await client.get("/admin/llm/spend?days=91", headers=headers)).status_code == 422
    assert (await client.get("/admin/llm/spend?days=30", headers=headers)).status_code == 200


# --- aggregation ------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_buckets_are_utc_and_deterministic(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(LlmConfig(singleton="default", daily_token_budget=100_000))
    fixture_id = await _seed_fixture(session, home="Arsenal", away="Chelsea")

    # Anchor to a recent UTC day (inside the window) but fix the *time of day*
    # explicitly so the assertion proves UTC-day bucketing regardless of the
    # server's timezone — never datetime.now() as the stored value.
    day1 = (datetime.now(UTC) - timedelta(days=3)).replace(
        hour=0, minute=30, second=0, microsecond=0
    )
    day1_late = day1.replace(hour=23, minute=30)  # same UTC day, late
    day2 = (day1 + timedelta(days=1)).replace(hour=12, minute=0)  # next UTC day

    # The two same-day rows must collapse into one bucket; the third is separate.
    await _seed_generation(session, fixture_id=fixture_id, created_at=day1, cost="0.10")
    await _seed_generation(session, fixture_id=fixture_id, created_at=day1_late, cost="0.20")
    await _seed_generation(session, fixture_id=fixture_id, created_at=day2, cost="0.05")
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.get("/admin/llm/spend?days=30", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    by_day = {d["day"]: d for d in body["daily"]}
    k1 = day1.date().isoformat()
    k2 = day2.date().isoformat()
    assert by_day[k1]["count"] == 2
    assert Decimal(by_day[k1]["cost"]) == Decimal("0.30")
    assert by_day[k1]["tokens_in"] == 200
    assert by_day[k2]["count"] == 1
    assert Decimal(by_day[k2]["cost"]) == Decimal("0.05")
    assert body["daily_token_budget"] == 100_000


@pytest.mark.asyncio
async def test_top_fixtures_ranked_by_cost(client: AsyncClient, session: AsyncSession) -> None:
    session.add(LlmConfig(singleton="default"))
    cheap = await _seed_fixture(session, home="Cheap FC", away="Rival")
    pricey = await _seed_fixture(session, home="Pricey FC", away="Rival")
    at = (datetime.now(UTC) - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    await _seed_generation(session, fixture_id=cheap, created_at=at, cost="0.05")
    await _seed_generation(session, fixture_id=pricey, created_at=at, cost="0.90")
    await session.commit()
    headers = await _admin_headers(session)

    body = (await client.get("/admin/llm/spend?days=30", headers=headers)).json()

    tops = body["top_fixtures"]
    assert tops[0]["home"] == "Pricey FC"
    assert Decimal(tops[0]["cost"]) == Decimal("0.90")
    assert tops[0]["league"] == "Premier League"


@pytest.mark.asyncio
async def test_window_excludes_older_rows(client: AsyncClient, session: AsyncSession) -> None:
    session.add(LlmConfig(singleton="default"))
    fixture_id = await _seed_fixture(session, home="A", away="B")
    now = datetime.now(UTC)
    await _seed_generation(
        session, fixture_id=fixture_id, created_at=now - timedelta(days=2), cost="0.10"
    )
    await _seed_generation(
        session, fixture_id=fixture_id, created_at=now - timedelta(days=40), cost="9.99"
    )
    await session.commit()
    headers = await _admin_headers(session)

    body = (await client.get("/admin/llm/spend?days=7", headers=headers)).json()
    assert Decimal(body["total_cost"]) == Decimal("0.10")  # the 40-day-old row is excluded


# --- generations through the service (ER-H-02) ------------------------------


async def _generate(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    fixture_id: uuid.UUID,
    steps: list[tuple[str, datetime]],
) -> None:
    async def _fake(config: LlmConfig, *, system: str, user: str) -> tuple[str, int, int]:
        return "text", 100, 50

    monkeypatch.setattr(analysis_service, "generate_completion", _fake)
    for language, now in steps:
        await get_or_create_analysis(
            session, get_redis(), fixture_id=fixture_id, language=language, now=now
        )
        await session.commit()


async def _with_consensus(session: AsyncSession, fixture_id: uuid.UUID) -> None:
    for outcome, prob in (("home", "0.5"), ("draw", "0.3"), ("away", "0.2")):
        session.add(
            Prediction(
                fixture_id=fixture_id,
                method="consensus",
                market="1x2",
                outcome=outcome,
                probability=Decimal(prob),
                model_version="v1",
            )
        )
    await session.flush()


def _llm_config() -> LlmConfig:
    return LlmConfig(
        singleton="default",
        model="test-model",
        is_enabled=True,
        cache_ttl_seconds=100,
        cost_per_1k_in=Decimal("0.5"),
        cost_per_1k_out=Decimal("1.5"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_language", "second_after"),
    [("ru", timedelta(seconds=10)), ("en", timedelta(seconds=200))],
    ids=["language_change", "stale_cache"],
)
async def test_every_generation_counts_in_spend(
    client: AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    second_language: str,
    second_after: timedelta,
) -> None:
    # Before ER-H-02 the second generation overwrote the first one's row, so
    # spend showed one generation instead of two.
    session.add(_llm_config())
    fixture_id = await _seed_fixture(session, home="A", away="B")
    await _with_consensus(session, fixture_id)
    await session.commit()
    at = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
    await _generate(
        session, monkeypatch, fixture_id, [("en", at), (second_language, at + second_after)]
    )
    headers = await _admin_headers(session)

    body = (await client.get("/admin/llm/spend?days=7", headers=headers)).json()

    assert sum(d["count"] for d in body["daily"]) == 2
    assert body["total_tokens"] == 300
    assert Decimal(body["total_cost"]) == Decimal("0.25")
    assert body["top_fixtures"][0]["count"] == 2


@pytest.mark.asyncio
async def test_spend_keeps_the_cost_computed_at_write_time(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(_llm_config())
    fixture_id = await _seed_fixture(session, home="A", away="B")
    await _with_consensus(session, fixture_id)
    await session.commit()
    at = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
    await _generate(session, monkeypatch, fixture_id, [("en", at)])
    headers = await _admin_headers(session)
    before = (await client.get("/admin/llm/spend?days=7", headers=headers)).json()

    config = (await session.execute(select(LlmConfig))).scalar_one()
    config.cost_per_1k_in = Decimal("50")
    config.cost_per_1k_out = Decimal("150")
    await session.commit()
    after = (await client.get("/admin/llm/spend?days=7", headers=headers)).json()

    assert Decimal(before["total_cost"]) == Decimal("0.125")
    assert after["total_cost"] == before["total_cost"]
    assert after["daily"] == before["daily"]
