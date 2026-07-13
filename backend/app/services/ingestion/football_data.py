"""football-data.co.uk historical ingestion.

Idempotent: re-running the same CSV inserts nothing new (``ON CONFLICT DO
NOTHING`` on the fixture/odds identity keys). football-data.co.uk is the
canonical seed source, so it may create canonical teams/leagues on first sight —
each creation is logged as structured JSON (league, raw name, normalized name,
CSV row) so it is actionable, never silent.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixture import Fixture, FixtureStats, FixtureStatus
from app.models.market import Odds
from app.providers.dtos import FixtureDTO
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.providers.id_mapping import (
    get_or_create_canonical_league,
    get_or_create_canonical_team,
)

logger = logging.getLogger("ingestion.football_data")

# Canonical league code -> (display name, country). Only leagues football-data
# actually covers; RPL is intentionally absent (see ingest warning + docs).
LEAGUE_META: dict[str, tuple[str, str]] = {
    "EPL": ("Premier League", "England"),
    "LALIGA": ("La Liga", "Spain"),
    "SERIEA": ("Serie A", "Italy"),
    "BUNDESLIGA": ("Bundesliga", "Germany"),
    "LIGUE1": ("Ligue 1", "France"),
}

PROVIDER = FootballDataCoUkProvider.name


@dataclass(slots=True)
class IngestSummary:
    fixtures_inserted: int = 0
    fixtures_seen: int = 0
    odds_inserted: int = 0
    teams_created: int = 0
    leagues_created: int = 0
    groups: dict[str, int] = field(default_factory=dict)  # "LEAGUE season" -> fixtures

    def merge(self, other: IngestSummary) -> None:
        self.fixtures_inserted += other.fixtures_inserted
        self.fixtures_seen += other.fixtures_seen
        self.odds_inserted += other.odds_inserted
        self.teams_created += other.teams_created
        self.leagues_created += other.leagues_created
        for k, v in other.groups.items():
            self.groups[k] = self.groups.get(k, 0) + v


def _warn_created(kind: str, **ctx: object) -> None:
    logger.warning(json.dumps({"event": f"canonical_{kind}_created", **ctx}))


async def ingest_dtos(
    session: AsyncSession, dtos: list[FixtureDTO], *, league_code: str
) -> IngestSummary:
    summary = IngestSummary()
    name, country = LEAGUE_META[league_code]

    league, created = await get_or_create_canonical_league(
        session,
        provider=PROVIDER,
        code=league_code,
        name=name,
        raw_code=league_code,
        country=country,
    )
    if created:
        summary.leagues_created += 1
        _warn_created("league", league=league_code, name=name)

    for dto in dtos:
        summary.fixtures_seen += 1
        home, home_created = await get_or_create_canonical_team(
            session, provider=PROVIDER, raw_name=dto.home.raw_name, country=country
        )
        away, away_created = await get_or_create_canonical_team(
            session, provider=PROVIDER, raw_name=dto.away.raw_name, country=country
        )
        for team_dto, created_flag in ((dto.home, home_created), (dto.away, away_created)):
            if created_flag:
                summary.teams_created += 1
                _warn_created(
                    "team",
                    league=league_code,
                    season=dto.season,
                    raw_name=team_dto.raw_name,
                    normalized=team_dto.raw_name.strip().lower(),
                    csv_row=dto.source_row,
                )

        fixture_id, inserted = await _upsert_fixture(session, dto, league.id, home.id, away.id)
        if inserted:
            summary.fixtures_inserted += 1
            key = f"{league_code} {dto.season}"
            summary.groups[key] = summary.groups.get(key, 0) + 1
        summary.odds_inserted += await _upsert_odds(session, dto, fixture_id)
        if inserted and dto.stats is not None:
            await _insert_stats(session, dto, fixture_id)

    await session.flush()
    return summary


async def _upsert_fixture(
    session: AsyncSession,
    dto: FixtureDTO,
    league_id: uuid.UUID,
    home_id: uuid.UUID,
    away_id: uuid.UUID,
) -> tuple[uuid.UUID, bool]:
    stmt = (
        pg_insert(Fixture)
        .values(
            league_id=league_id,
            season=dto.season,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=dto.kickoff_at,
            status=FixtureStatus.finished,
            ft_home=dto.ft_home,
            ft_away=dto.ft_away,
            ht_home=dto.ht_home,
            ht_away=dto.ht_away,
            source=dto.provider,
        )
        .on_conflict_do_nothing(constraint="uq_fixture_identity")
        .returning(Fixture.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        return new_id, True

    existing = await session.scalar(
        select(Fixture.id).where(
            Fixture.league_id == league_id,
            Fixture.season == dto.season,
            Fixture.home_team_id == home_id,
            Fixture.away_team_id == away_id,
            Fixture.kickoff_at == dto.kickoff_at,
        )
    )
    if existing is None:  # pragma: no cover - the DO NOTHING conflict guarantees a row
        raise RuntimeError("fixture upsert conflict resolved to no row")
    return existing, False


async def _upsert_odds(session: AsyncSession, dto: FixtureDTO, fixture_id: uuid.UUID) -> int:
    inserted = 0
    for o in dto.odds:
        stmt = (
            pg_insert(Odds)
            .values(
                fixture_id=fixture_id,
                bookmaker=o.bookmaker,
                market=o.market,
                outcome=o.outcome,
                ts=o.ts,
                price=o.price,
                is_closing=o.is_closing,
            )
            .on_conflict_do_nothing()
            .returning(Odds.fixture_id)
        )
        result = await session.execute(stmt)
        inserted += len(result.fetchall())
    return inserted


async def _insert_stats(session: AsyncSession, dto: FixtureDTO, fixture_id: uuid.UUID) -> None:
    if dto.stats is None:
        return
    stmt = (
        pg_insert(FixtureStats)
        .values(
            fixture_id=fixture_id,
            home_shots=dto.stats.home_shots,
            away_shots=dto.stats.away_shots,
            home_shots_on_target=dto.stats.home_shots_on_target,
            away_shots_on_target=dto.stats.away_shots_on_target,
            home_corners=dto.stats.home_corners,
            away_corners=dto.stats.away_corners,
        )
        .on_conflict_do_nothing(index_elements=[FixtureStats.fixture_id])
    )
    await session.execute(stmt)
