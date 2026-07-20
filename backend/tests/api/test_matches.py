"""Public /matches list + detail endpoints (Phase 6)."""

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
from app.models.user import User, UserTier
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _tier_headers(session: AsyncSession, tier: UserTier) -> dict[str, str]:
    """Create a user on ``tier`` and return a bearer header for them."""
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=tier)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


# 1X2 probabilities per method, chosen so the home-win spread is easy to reason
# about: elo/glicko2/dixon_coles/xg/lightgbm cluster near 0.5, market is 0.40,
# consensus is 0.52.
_METHOD_HOME = {
    "elo": (0.50, 0.30, 0.20),
    "glicko2": (0.52, 0.28, 0.20),
    "dixon_coles": (0.48, 0.30, 0.22),
    "xg": (0.51, 0.29, 0.20),
    "lightgbm": (0.49, 0.31, 0.20),
    "market": (0.40, 0.30, 0.30),
    "consensus": (0.52, 0.28, 0.20),
}


async def _seed_registry(session: AsyncSession) -> None:
    session.add_all(
        [
            ModelRegistry(
                method=method,
                version="v1",
                status=(ModelStatus.champion if method == "lightgbm" else ModelStatus.challenger),
                is_enabled=True,
                is_visible=(method != "glicko2"),  # glicko2 hidden → excluded from card
                display_weight=Decimal("10"),
                accuracy_pct=Decimal("60.0") if method == "lightgbm" else Decimal("40.0"),
                sample_count=400,
            )
            for method in _METHOD_HOME
        ]
    )


async def _seed_match(
    session: AsyncSession,
    *,
    league_code: str = "EPL",
    kickoff: datetime | None = None,
    status: FixtureStatus = FixtureStatus.scheduled,
    with_predictions: bool = True,
    methods: tuple[str, ...] = tuple(_METHOD_HOME),
    last_polled_at: datetime | None = None,
    home: str = "Arsenal",
    away: str = "Chelsea",
) -> Fixture:
    kickoff = kickoff or datetime.now(UTC) + timedelta(hours=6)
    league_obj = (
        await session.execute(select(League).where(League.code == league_code))
    ).scalar_one_or_none()
    if league_obj is None:
        league_obj = League(code=league_code, name=f"{league_code} League")
        session.add(league_obj)
        await session.flush()
    home_team = Team(name=home, normalized_name=f"{home}-{uuid.uuid4().hex[:6]}")
    away_team = Team(name=away, normalized_name=f"{away}-{uuid.uuid4().hex[:6]}")
    session.add_all([home_team, away_team])
    await session.flush()

    fixture = Fixture(
        league_id=league_obj.id,
        season="2025-2026",
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        kickoff_at=kickoff,
        status=status,
        minute=55 if status == FixtureStatus.live else None,
        last_polled_at=last_polled_at,
    )
    session.add(fixture)
    await session.flush()

    if with_predictions:
        for method in methods:
            h, d, a = _METHOD_HOME[method]
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
    return fixture


@pytest.mark.asyncio
async def test_list_returns_only_fixtures_with_predictions(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_registry(session)
    await _seed_match(session, home="A", away="B")  # has predictions
    await _seed_match(session, with_predictions=False, home="C", away="D")  # excluded
    await session.commit()

    resp = await client.get("/matches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["consensus"]["home"] == pytest.approx(0.52)
    assert item["champion_method"] == "lightgbm"
    assert item["champion_accuracy_pct"] == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_list_league_and_status_filters(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_registry(session)
    await _seed_match(
        session, league_code="EPL", status=FixtureStatus.scheduled, home="A", away="B"
    )
    await _seed_match(session, league_code="LALIGA", status=FixtureStatus.live, home="C", away="D")
    await session.commit()

    epl = (await client.get("/matches", params={"league": "EPL"})).json()
    assert epl["total"] == 1
    assert epl["items"][0]["league"]["code"] == "EPL"

    live = (await client.get("/matches", params={"status": "live"})).json()
    assert live["total"] == 1
    assert live["items"][0]["status"] == "live"


@pytest.mark.asyncio
async def test_list_league_filter_treats_sql_payload_as_data(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_registry(session)
    await _seed_match(session, league_code="EPL")
    await session.commit()

    payload = "EPL' OR '1'='1"
    resp = await client.get("/matches", params={"league": payload})

    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_list_default_window_excludes_far_future(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_registry(session)
    await _seed_match(session, kickoff=datetime.now(UTC) + timedelta(hours=6), home="A", away="B")
    await _seed_match(session, kickoff=datetime.now(UTC) + timedelta(days=10), home="C", away="D")
    await session.commit()

    default = (await client.get("/matches")).json()
    assert default["total"] == 1

    # An explicit date targets exactly that day.
    far = datetime.now(UTC) + timedelta(days=10)
    dated = (await client.get("/matches", params={"date": far.strftime("%Y-%m-%d")})).json()
    assert dated["total"] == 1


@pytest.mark.asyncio
async def test_list_pagination(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_registry(session)
    base = datetime.now(UTC) + timedelta(hours=1)
    for i in range(3):
        await _seed_match(session, kickoff=base + timedelta(minutes=i), home=f"H{i}", away=f"A{i}")
    await session.commit()

    page1 = (await client.get("/matches", params={"limit": 2, "offset": 0})).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["limit"] == 2

    page2 = (await client.get("/matches", params={"limit": 2, "offset": 2})).json()
    assert len(page2["items"]) == 1


@pytest.mark.asyncio
async def test_list_bad_date_is_422(client: AsyncClient, session: AsyncSession) -> None:
    resp = await client.get("/matches", params={"date": "2026/07/15"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_detail_visible_methods_and_consensus(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_registry(session)
    fixture = await _seed_match(session)
    # Method bars are pro/expert-gated; authenticate as pro to receive them.
    headers = await _tier_headers(session, UserTier.pro)

    resp = await client.get(f"/matches/{fixture.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    method_names = {m["method"] for m in body["methods"]}
    # consensus + market are broken out separately; glicko2 is is_visible=False.
    assert "consensus" not in method_names
    assert "market" not in method_names
    assert "glicko2" not in method_names
    assert {"elo", "dixon_coles", "xg", "lightgbm"} <= method_names

    # Champion sorts first and is flagged.
    assert body["methods"][0]["method"] == "lightgbm"
    assert body["methods"][0]["is_champion"] is True

    assert body["consensus"]["home"] == pytest.approx(0.52)
    assert body["market"]["home"] == pytest.approx(0.40)
    assert body["tier_required"] == "pro"
    assert body["flags"]["methods"] == "all"
    # delta = consensus.home - market.home = 0.52 - 0.40.
    assert body["delta_vs_market"] == pytest.approx(0.12)
    # Methods cluster tightly → high agreement.
    assert body["model_agreement_pct"] > 95.0


@pytest.mark.asyncio
async def test_detail_delta_null_without_market(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_registry(session)
    methods = tuple(m for m in _METHOD_HOME if m != "market")
    fixture = await _seed_match(session, methods=methods)
    await session.commit()

    body = (await client.get(f"/matches/{fixture.id}")).json()
    assert body["market"] is None
    assert body["delta_vs_market"] is None


@pytest.mark.asyncio
async def test_detail_unknown_fixture_404(client: AsyncClient, session: AsyncSession) -> None:
    resp = await client.get(f"/matches/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_data_delayed_flag(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_registry(session)
    stale = await _seed_match(
        session,
        status=FixtureStatus.live,
        last_polled_at=datetime.now(UTC) - timedelta(minutes=10),
        home="Stale",
        away="X",
    )
    fresh = await _seed_match(
        session,
        status=FixtureStatus.live,
        last_polled_at=datetime.now(UTC) - timedelta(seconds=30),
        home="Fresh",
        away="Y",
    )
    never = await _seed_match(session, status=FixtureStatus.scheduled, home="Never", away="Z")
    await session.commit()

    stale_body = (await client.get(f"/matches/{stale.id}")).json()
    fresh_body = (await client.get(f"/matches/{fresh.id}")).json()
    never_body = (await client.get(f"/matches/{never.id}")).json()

    assert stale_body["data_delayed"] is True
    assert fresh_body["data_delayed"] is False
    assert never_body["data_delayed"] is False
    assert never_body["last_polled_at"] is None
