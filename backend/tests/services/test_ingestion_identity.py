"""Provider-agnostic fixture identity (HI-2).

Seasons, UTC kickoffs, the fail-closed licence gate, cross-source dedup
(including the ±36 h boundary and two-legged pairs), team identity across
sources and the unmapped-team worklist — against real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.core.config import get_settings
from app.core.seasons import SeasonFormatError, canonical_season, is_canonical
from app.models.audit_log import AuditLog
from app.models.fixture import Fixture, FixtureExternalRef
from app.models.reference import (
    League,
    ProviderLeagueAlias,
    ProviderTeamAlias,
    ProviderUnmappedTeam,
    Team,
)
from app.providers.dtos import BookmakerOddsDTO, FixtureDTO, LeagueRef, LiveFixtureDTO, TeamRef
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.providers.id_mapping import (
    UnmappedEntityError,
    get_or_create_canonical_team,
    map_team,
    resolve_team,
)
from app.services.ingestion.core import (
    LeagueMeta,
    SourceNotLicensed,
    ensure_licensed,
    ingest_from_adapter,
    ingest_records,
)
from app.services.live.ingestion import poll_live
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

EPL = LeagueMeta("Premier League", "England", 8)
KICKOFF = datetime(2026, 9, 20, 14, 0, tzinfo=UTC)


# --- seasons -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "split", "expected"),
    [
        ("2026", True, "2026-2027"),
        ("2026-2027", True, "2026-2027"),
        ("2026/2027", True, "2026-2027"),
        ("2026/27", True, "2026-2027"),
        ("26/27", True, "2026-2027"),
        ("1999/00", True, "1999-2000"),
        ("2026", False, "2026"),
    ],
)
def test_canonical_season(raw: str, split: bool, expected: str) -> None:
    assert canonical_season(raw, split_year=split) == expected
    assert is_canonical(expected, split_year=split)


@pytest.mark.parametrize(
    ("raw", "split"), [("2026-2028", True), ("2026-2027", False), ("season 26", True), ("", True)]
)
def test_unreadable_or_inconsistent_seasons_are_rejected(raw: str, split: bool) -> None:
    with pytest.raises(SeasonFormatError):
        canonical_season(raw, split_year=split)


# --- UTC kickoffs --------------------------------------------------------------


def test_naive_kickoff_is_rejected() -> None:
    with pytest.raises(ValidationError, match="time-zone aware"):
        FixtureDTO(
            provider="x",
            league=LeagueRef(raw_name="EPL"),
            season="2026-2027",
            home=TeamRef(raw_name="A"),
            away=TeamRef(raw_name="B"),
            kickoff_at=datetime(2026, 9, 20, 14, 0),
        )


def test_aware_kickoff_is_stored_as_utc() -> None:
    dto = FixtureDTO(
        provider="x",
        league=LeagueRef(raw_name="EPL"),
        season="2026-2027",
        home=TeamRef(raw_name="A"),
        away=TeamRef(raw_name="B"),
        kickoff_at=datetime.fromisoformat("2026-09-20T16:00:00+02:00"),
    )
    assert dto.kickoff_at == KICKOFF and dto.kickoff_at.tzinfo == UTC


_CSV_HEAD = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG\n"


def test_football_data_converts_uk_time_and_marks_date_only() -> None:
    provider = FootballDataCoUkProvider()
    with_time = provider.parse_csv(
        (
            _CSV_HEAD
            + "E0,11/08/2023,20:00,Burnley,Man City,0,3,0,1\n"
            + "E0,26/12/2023,15:00,Man City,Everton,2,1,1,0\n"
        ).encode(),
        "EPL",
        "2023-2024",
    )
    summer, winter = with_time
    assert summer.kickoff_at == datetime(2023, 8, 11, 19, 0, tzinfo=UTC)  # BST
    assert winter.kickoff_at == datetime(2023, 12, 26, 15, 0, tzinfo=UTC)  # GMT
    assert summer.kickoff_time_known and winter.kickoff_time_known
    assert summer.external_id == "fd:E0:2023-2024:20230811:burnley:man city"

    (date_only, same_day) = provider.parse_csv(
        (
            b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
            b"E0,08/08/2015,Bournemouth,Aston Villa,0,1\n"
            b"E0,08/08/2015,Chelsea,Swansea,2,2\n"
        ),
        "EPL",
        "2015-2016",
    )
    assert date_only.kickoff_at == datetime(2015, 8, 8, 12, 0, tzinfo=UTC)
    assert date_only.kickoff_time_known is False
    # Same-day date-only fixtures share one instant: one ML chronology batch.
    assert same_day.kickoff_at == date_only.kickoff_at


# --- licence gate (fail closed) ------------------------------------------------


class _Adapter:
    name = "paid_source"
    may_seed_canonical = True

    def __init__(self, records: list[FixtureDTO], **attrs: Any) -> None:
        self.records = records
        for key, value in attrs.items():
            setattr(self, key, value)

    async def fetch(self, league_code: str, season: str) -> list[FixtureDTO]:
        return self.records


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "environment", "production")


@pytest.mark.usefixtures("production")
@pytest.mark.parametrize(
    "attrs", [{}, {"licensed_for_production": False}, {"licensed_for_production": "yes"}]
)
def test_adapter_without_an_explicit_licence_is_rejected_in_production(
    attrs: dict[str, Any],
) -> None:
    with pytest.raises(SourceNotLicensed):
        ensure_licensed(_Adapter([], **attrs))


@pytest.mark.usefixtures("production")
def test_explicitly_licensed_adapter_runs_in_production() -> None:
    ensure_licensed(_Adapter([], licensed_for_production=True))


def test_unlicensed_adapter_runs_outside_production() -> None:
    ensure_licensed(_Adapter([]))  # development (tests): allowed
    ensure_licensed(FootballDataCoUkProvider())


@pytest.mark.asyncio
@pytest.mark.usefixtures("production")
async def test_football_data_is_refused_in_production(session: AsyncSession) -> None:
    from app.services.ingestion.football_data import ingest_dtos

    with pytest.raises(SourceNotLicensed):
        await ingest_dtos(session, [], league_code="EPL")
    with pytest.raises(SourceNotLicensed):
        await ingest_from_adapter(
            session, FootballDataCoUkProvider(), league_code="EPL", season="2025-2026"
        )
    assert await session.scalar(select(func.count()).select_from(Fixture)) == 0


# --- cross-source dedup ----------------------------------------------------------


def _dto(
    provider: str,
    external_id: str,
    *,
    kickoff: datetime = KICKOFF,
    season: str = "2026-2027",
    home: str = "Arsenal",
    away: str = "Tottenham",
    league_code: str = "EPL",
) -> FixtureDTO:
    return FixtureDTO(
        provider=provider,
        external_id=external_id,
        league=LeagueRef(raw_name=league_code, raw_code=league_code),
        season=season,
        home=TeamRef(raw_name=home),
        away=TeamRef(raw_name=away),
        kickoff_at=kickoff,
        ft_home=2,
        ft_away=1,
        odds=[
            BookmakerOddsDTO(
                bookmaker="market_avg", market="1x2", outcome=o, price=Decimal(p), ts=kickoff
            )
            for o, p in (("home", "2.0"), ("draw", "3.4"), ("away", "3.8"))
        ],
    )


async def _seed_source_a(session: AsyncSession) -> uuid.UUID:
    """Source A seeds league + teams and the canonical fixture."""
    await ingest_records(
        session, [_dto("src_a", "a-1")], provider="src_a", league_code="EPL", seed=True, meta=EPL
    )
    return (await session.execute(select(Fixture.id))).scalar_one()


async def _alias_source_b(
    session: AsyncSession, *, home: str = "Arsenal FC", away: str = "Spurs"
) -> None:
    """Source B names the teams differently; aliases map them to the same rows."""
    league = (await session.execute(select(League).where(League.code == "EPL"))).scalar_one()
    arsenal = (
        await session.execute(select(Team).where(Team.normalized_name == "arsenal"))
    ).scalar_one()
    spurs = (
        await session.execute(select(Team).where(Team.normalized_name == "tottenham"))
    ).scalar_one()
    session.add_all(
        [
            ProviderLeagueAlias(provider="src_b", alias="EPL", league_id=league.id),
            ProviderTeamAlias(provider="src_b", alias=home, team_id=arsenal.id),
            ProviderTeamAlias(provider="src_b", alias=away, team_id=spurs.id),
        ]
    )
    await session.flush()


async def _ingest_b(
    session: AsyncSession, external_id: str, kickoff: datetime, season: str = "2026"
) -> Any:
    return await ingest_records(
        session,
        [
            _dto(
                "src_b",
                external_id,
                kickoff=kickoff,
                season=season,
                home="Arsenal FC",
                away="Spurs",
            )
        ],
        provider="src_b",
        league_code="EPL",
        seed=False,
    )


@pytest.mark.asyncio
async def test_reingest_is_idempotent_by_external_ref(session: AsyncSession) -> None:
    fixture_id = await _seed_source_a(session)
    again = await ingest_records(
        session, [_dto("src_a", "a-1")], provider="src_a", league_code="EPL", seed=True, meta=EPL
    )
    assert (again.fixtures_inserted, again.fixtures_linked, again.odds_inserted) == (0, 0, 0)
    assert (await session.execute(select(Fixture.id))).scalars().all() == [fixture_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_hours", [0, 1, -35, 35])
async def test_other_source_within_36h_links_to_the_same_fixture(
    session: AsyncSession, offset_hours: int
) -> None:
    fixture_id = await _seed_source_a(session)
    await _alias_source_b(session)

    summary = await _ingest_b(session, "b-77", KICKOFF + timedelta(hours=offset_hours))

    assert (summary.fixtures_inserted, summary.fixtures_linked) == (0, 1)
    assert (await session.execute(select(Fixture.id))).scalars().all() == [fixture_id]
    refs = {
        (r.provider, r.external_id)
        for r in (await session.execute(select(FixtureExternalRef))).scalars()
    }
    assert refs == {("src_a", "a-1"), ("src_b", "b-77")}
    # Canonical season stays; B's "2026" never created a second label.
    assert (await session.execute(select(Fixture.season))).scalar_one() == "2026-2027"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_hours", [37, -37, 24 * 7])
async def test_same_pair_beyond_36h_is_a_different_fixture(
    session: AsyncSession, offset_hours: int
) -> None:
    """37 h — or a second leg a week later — is never merged."""
    await _seed_source_a(session)
    await _alias_source_b(session)

    summary = await _ingest_b(session, "b-78", KICKOFF + timedelta(hours=offset_hours))

    assert (summary.fixtures_inserted, summary.fixtures_linked) == (1, 0)
    assert await session.scalar(select(func.count()).select_from(Fixture)) == 2


@pytest.mark.asyncio
async def test_two_legs_from_one_source_stay_two_fixtures(session: AsyncSession) -> None:
    legs = [_dto("src_a", "leg-1"), _dto("src_a", "leg-2", kickoff=KICKOFF + timedelta(days=7))]
    summary = await ingest_records(
        session, legs, provider="src_a", league_code="EPL", seed=True, meta=EPL
    )
    assert summary.fixtures_inserted == 2


@pytest.mark.asyncio
async def test_record_without_external_id_is_rejected(session: AsyncSession) -> None:
    dto = _dto("src_a", "x").model_copy(update={"external_id": None})
    with pytest.raises(ValueError, match="external_id"):
        await ingest_records(
            session, [dto], provider="src_a", league_code="EPL", seed=True, meta=EPL
        )


# --- team identity across sources ------------------------------------------------


@pytest.mark.asyncio
async def test_same_name_in_two_countries_is_two_teams(session: AsyncSession) -> None:
    spain, created_es = await get_or_create_canonical_team(
        session, provider="src_a", raw_name="Racing", country="Spain"
    )
    argentina, created_ar = await get_or_create_canonical_team(
        session, provider="src_c", raw_name="Racing", country="Argentina"
    )
    assert created_es and created_ar and spain.id != argentina.id
    again, created = await get_or_create_canonical_team(
        session, provider="src_d", raw_name="Racing", country="Spain"
    )
    assert again.id == spain.id and not created


@pytest.mark.asyncio
async def test_stable_external_team_id_survives_a_rename(session: AsyncSession) -> None:
    team, _ = await get_or_create_canonical_team(
        session, provider="src_a", raw_name="Wolves", country="England"
    )
    session.add(
        ProviderTeamAlias(
            provider="src_b", alias="Wolverhampton", external_id="39", team_id=team.id
        )
    )
    await session.flush()
    renamed = await resolve_team(session, "src_b", "Wolverhampton Wanderers FC", external_id="39")
    assert renamed.id == team.id


@pytest.mark.asyncio
async def test_strict_resolution_records_unmapped_and_map_team_clears_it(
    session: AsyncSession,
) -> None:
    team, _ = await get_or_create_canonical_team(
        session, provider="src_a", raw_name="Brighton", country="England"
    )
    for _ in range(2):
        with pytest.raises(UnmappedEntityError):
            await resolve_team(
                session, "src_b", "Brighton & Hove Albion", external_id="51", league_hint="EPL"
            )
    unmapped = (await session.execute(select(ProviderUnmappedTeam))).scalar_one()
    assert (unmapped.raw_name, unmapped.external_id, unmapped.seen_count) == (
        "Brighton & Hove Albion",
        "51",
        2,
    )
    assert await session.scalar(select(func.count()).select_from(Team)) == 1  # nothing created

    await map_team(
        session,
        provider="src_b",
        raw_name="Brighton & Hove Albion",
        team_id=team.id,
        external_id="51",
    )
    assert await session.scalar(select(func.count()).select_from(ProviderUnmappedTeam)) == 0
    resolved = await resolve_team(session, "src_b", "Brighton & Hove Albion")
    assert resolved.id == team.id
    audit = (
        await session.execute(select(AuditLog).where(AuditLog.action == "ingestion.team.mapped"))
    ).scalar_one()
    assert audit.meta["team_id"] == str(team.id)


@pytest.mark.asyncio
async def test_unmapped_team_blocks_the_record_without_creating_anything(
    session: AsyncSession,
) -> None:
    await _seed_source_a(session)
    league = (await session.execute(select(League))).scalar_one()
    session.add(ProviderLeagueAlias(provider="src_b", alias="EPL", league_id=league.id))
    await session.flush()
    with pytest.raises(UnmappedEntityError):
        await _ingest_b(session, "b-1", KICKOFF)
    assert await session.scalar(select(func.count()).select_from(Fixture)) == 1


# --- live ingestion uses the same identity -------------------------------------


@pytest.mark.asyncio
async def test_live_poll_canonicalises_season_and_never_duplicates_history(
    session: AsyncSession,
) -> None:
    fixture_id = await _seed_source_a(session)
    league = (await session.execute(select(League))).scalar_one()
    arsenal = (
        await session.execute(select(Team).where(Team.normalized_name == "arsenal"))
    ).scalar_one()
    spurs = (
        await session.execute(select(Team).where(Team.normalized_name == "tottenham"))
    ).scalar_one()
    session.add_all(
        [
            ProviderLeagueAlias(provider="api_football", alias="39", league_id=league.id),
            ProviderTeamAlias(provider="api_football", alias="Arsenal", team_id=arsenal.id),
            ProviderTeamAlias(provider="api_football", alias="Tottenham", team_id=spurs.id),
        ]
    )
    await session.flush()

    live = LiveFixtureDTO(
        provider="api_football",
        provider_fixture_id="1035000",
        league=LeagueRef(raw_name="Premier League", raw_code="39"),
        season="2026",
        home=TeamRef(raw_name="Arsenal"),
        away=TeamRef(raw_name="Tottenham"),
        kickoff_at=KICKOFF + timedelta(minutes=30),
        minute=12,
        home_score=0,
        away_score=0,
    )
    for _ in range(2):  # re-polling is idempotent
        result = await poll_live(session, provider=None, dtos=[live])  # type: ignore[arg-type]
        assert result.ingested == 1

    fixtures = (await session.execute(select(Fixture))).scalars().all()
    assert [f.id for f in fixtures] == [fixture_id]
    assert fixtures[0].season == "2026-2027" and fixtures[0].minute == 12
    refs = {
        (r.provider, r.external_id)
        for r in (await session.execute(select(FixtureExternalRef))).scalars()
    }
    assert ("api_football", "1035000") in refs


@pytest.mark.asyncio
async def test_live_poll_creates_a_new_fixture_with_canonical_season(
    session: AsyncSession,
) -> None:
    league = League(code="EPL", name="Premier League", season_start_month=8)
    home = Team(name="Arsenal", normalized_name="arsenal", country="England")
    away = Team(name="Chelsea", normalized_name="chelsea", country="England")
    session.add_all([league, home, away])
    await session.flush()
    session.add_all(
        [
            ProviderLeagueAlias(provider="api_football", alias="39", league_id=league.id),
            ProviderTeamAlias(provider="api_football", alias="Arsenal", team_id=home.id),
            ProviderTeamAlias(provider="api_football", alias="Chelsea", team_id=away.id),
        ]
    )
    await session.flush()
    live = LiveFixtureDTO(
        provider="api_football",
        provider_fixture_id="9",
        league=LeagueRef(raw_name="Premier League", raw_code="39"),
        season="2026",
        home=TeamRef(raw_name="Arsenal"),
        away=TeamRef(raw_name="Chelsea"),
        kickoff_at=KICKOFF,
        minute=1,
        home_score=0,
        away_score=0,
    )
    await poll_live(session, provider=None, dtos=[live])  # type: ignore[arg-type]
    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert fixture.season == "2026-2027"
