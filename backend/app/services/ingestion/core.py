"""Provider-agnostic fixture ingestion.

Every historical source plugs in as a :class:`SourceAdapter` that turns
``(league, season)`` into :class:`~app.providers.dtos.FixtureDTO` records; this
module owns identity, so a new (paid) provider needs an adapter, not a schema
change.

Identity of a record, in order:

1. ``fixture_external_refs`` — the source's own id for the fixture;
2. **cross-source dedup** — the same league and canonical teams with a kickoff
   within ±36 h (any season label) is the same match from another source; the
   new external id is linked to it;
3. otherwise a new fixture is inserted (``uq_fixture_identity`` stays the last
   guard).

Seasons are canonicalised per league (:mod:`app.core.seasons`) and kickoffs are
time-zone-aware UTC (the DTO rejects naive datetimes).

**Licence gate — fails closed:** in the ``production`` environment an adapter
is used only if it declares ``licensed_for_production = True`` explicitly; a
missing or false flag is rejected with :class:`SourceNotLicensed`.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.seasons import canonical_season
from app.models.fixture import Fixture, FixtureExternalRef, FixtureStats, FixtureStatus
from app.models.market import Odds
from app.models.reference import League
from app.providers.dtos import FixtureDTO
from app.providers.id_mapping import (
    get_or_create_canonical_league,
    get_or_create_canonical_team,
    normalize_name,
    resolve_league,
    resolve_team,
)

logger = logging.getLogger("ingestion.core")

# Same pairing in the same league this close in time is one match.
DEDUP_WINDOW = timedelta(hours=36)


class SourceNotLicensed(Exception):
    """An adapter not explicitly licensed for production was used there."""


class SourceAdapter(Protocol):
    """A historical source. ``licensed_for_production`` must be set to ``True``
    explicitly for the adapter to run in production (fail closed);
    ``may_seed_canonical`` allows creating canonical leagues/teams — otherwise
    names resolve strictly through aliases."""

    name: str
    licensed_for_production: bool
    may_seed_canonical: bool

    async def fetch(self, league_code: str, season: str) -> list[FixtureDTO]: ...


def ensure_licensed(adapter: object, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.environment != "production":
        return
    if getattr(adapter, "licensed_for_production", False) is not True:
        name = getattr(adapter, "name", type(adapter).__name__)
        raise SourceNotLicensed(
            f"source '{name}' is not licensed for production use; refusing to ingest"
        )


@dataclass(frozen=True, slots=True)
class LeagueMeta:
    name: str
    country: str
    season_start_month: int | None


@dataclass(slots=True)
class IngestSummary:
    fixtures_inserted: int = 0
    fixtures_seen: int = 0
    fixtures_linked: int = 0  # matched an existing fixture from another source
    odds_inserted: int = 0
    teams_created: int = 0
    leagues_created: int = 0
    groups: dict[str, int] = field(default_factory=dict)  # "LEAGUE season" -> inserted

    def merge(self, other: IngestSummary) -> None:
        self.fixtures_inserted += other.fixtures_inserted
        self.fixtures_seen += other.fixtures_seen
        self.fixtures_linked += other.fixtures_linked
        self.odds_inserted += other.odds_inserted
        self.teams_created += other.teams_created
        self.leagues_created += other.leagues_created
        for k, v in other.groups.items():
            self.groups[k] = self.groups.get(k, 0) + v


def _warn(event: str, **ctx: object) -> None:
    logger.warning(json.dumps({"event": event, **ctx}, default=str))


def canonical_season_for(league: League, raw: str) -> str:
    return canonical_season(raw, split_year=league.season_start_month is not None)


async def find_fixture(
    session: AsyncSession,
    *,
    provider: str,
    external_id: str | None,
    league_id: uuid.UUID,
    home_id: uuid.UUID,
    away_id: uuid.UUID,
    kickoff: datetime,
) -> tuple[Fixture | None, bool]:
    """``(fixture, by_ref)``: the fixture this record denotes, if any, and
    whether it was found by the source's own id (else by cross-source dedup).
    The dedup picks the closest kickoff within the window."""
    if external_id is not None:
        fixture = (
            await session.execute(
                select(Fixture)
                .join(FixtureExternalRef, FixtureExternalRef.fixture_id == Fixture.id)
                .where(
                    FixtureExternalRef.provider == provider,
                    FixtureExternalRef.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if fixture is not None:
            return fixture, True
    distance = func.abs(func.extract("epoch", Fixture.kickoff_at - kickoff))
    fixture = (
        await session.execute(
            select(Fixture)
            .where(
                Fixture.league_id == league_id,
                Fixture.home_team_id == home_id,
                Fixture.away_team_id == away_id,
                distance <= DEDUP_WINDOW.total_seconds(),
            )
            .order_by(distance, Fixture.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return fixture, False


async def link_external_ref(
    session: AsyncSession, *, fixture_id: uuid.UUID, provider: str, external_id: str
) -> None:
    await session.execute(
        pg_insert(FixtureExternalRef)
        .values(id=uuid.uuid4(), fixture_id=fixture_id, provider=provider, external_id=external_id)
        .on_conflict_do_nothing(constraint="uq_fixture_external_ref")
    )


async def _resolve_entities(
    session: AsyncSession,
    dto: FixtureDTO,
    *,
    provider: str,
    league_code: str,
    seed: bool,
    meta: LeagueMeta | None,
    summary: IngestSummary,
) -> tuple[League, uuid.UUID, uuid.UUID]:
    if seed:
        if meta is None:
            raise ValueError(f"seed source needs league metadata for '{league_code}'")
        league, created = await get_or_create_canonical_league(
            session,
            provider=provider,
            code=league_code,
            name=meta.name,
            raw_code=dto.league.raw_code or league_code,
            country=meta.country,
            season_start_month=meta.season_start_month,
        )
        if created:
            summary.leagues_created += 1
            _warn("canonical_league_created", league=league_code, name=meta.name)
        team_ids: list[uuid.UUID] = []
        for ref in (dto.home, dto.away):
            team, team_created = await get_or_create_canonical_team(
                session,
                provider=provider,
                raw_name=ref.raw_name,
                country=meta.country,
                external_id=ref.external_id,
            )
            if team_created:
                summary.teams_created += 1
                _warn(
                    "canonical_team_created",
                    league=league_code,
                    season=dto.season,
                    raw_name=ref.raw_name,
                    normalized=normalize_name(ref.raw_name),
                    csv_row=dto.source_row,
                )
            team_ids.append(team.id)
        return league, team_ids[0], team_ids[1]

    league = await resolve_league(session, provider, dto.league.raw_code or league_code)
    home = await resolve_team(
        session,
        provider,
        dto.home.raw_name,
        external_id=dto.home.external_id,
        league_hint=league_code,
    )
    away = await resolve_team(
        session,
        provider,
        dto.away.raw_name,
        external_id=dto.away.external_id,
        league_hint=league_code,
    )
    return league, home.id, away.id


async def ingest_records(
    session: AsyncSession,
    records: list[FixtureDTO],
    *,
    provider: str,
    league_code: str,
    seed: bool,
    meta: LeagueMeta | None = None,
) -> IngestSummary:
    """Ingest one source's records for one league. Idempotent: a re-run finds
    every fixture by its external ref and inserts nothing new."""
    summary = IngestSummary()
    for dto in records:
        summary.fixtures_seen += 1
        if not dto.external_id:
            raise ValueError(f"{provider} record without external_id (row {dto.source_row})")
        league, home_id, away_id = await _resolve_entities(
            session,
            dto,
            provider=provider,
            league_code=league_code,
            seed=seed,
            meta=meta,
            summary=summary,
        )
        season = canonical_season_for(league, dto.season)

        fixture, by_ref = await find_fixture(
            session,
            provider=provider,
            external_id=dto.external_id,
            league_id=league.id,
            home_id=home_id,
            away_id=away_id,
            kickoff=dto.kickoff_at,
        )
        inserted = False
        if fixture is None:
            fixture_id = await _insert_fixture(session, dto, league.id, season, home_id, away_id)
            inserted = fixture_id is not None
            if fixture_id is None:  # pragma: no cover - lost a race on uq_fixture_identity
                fixture, _ = await find_fixture(
                    session,
                    provider=provider,
                    external_id=None,
                    league_id=league.id,
                    home_id=home_id,
                    away_id=away_id,
                    kickoff=dto.kickoff_at,
                )
                if fixture is None:
                    raise RuntimeError("fixture insert conflicted but no fixture was found")
                fixture_id = fixture.id
        else:
            fixture_id = fixture.id
            if not by_ref:
                summary.fixtures_linked += 1
                _warn(
                    "fixture_linked_across_sources",
                    provider=provider,
                    external_id=dto.external_id,
                    fixture_id=str(fixture.id),
                    existing_season=fixture.season,
                    existing_kickoff=fixture.kickoff_at.isoformat(),
                    kickoff=dto.kickoff_at.isoformat(),
                )
        await link_external_ref(
            session, fixture_id=fixture_id, provider=provider, external_id=dto.external_id
        )

        if inserted:
            summary.fixtures_inserted += 1
            key = f"{league_code} {season}"
            summary.groups[key] = summary.groups.get(key, 0) + 1
            if dto.stats is not None:
                await session.execute(
                    pg_insert(FixtureStats)
                    .values(id=uuid.uuid4(), fixture_id=fixture_id, **dto.stats.model_dump())
                    .on_conflict_do_nothing(index_elements=[FixtureStats.fixture_id])
                )
        summary.odds_inserted += await _insert_odds(session, dto, fixture_id)

    await session.flush()
    return summary


async def _insert_fixture(
    session: AsyncSession,
    dto: FixtureDTO,
    league_id: uuid.UUID,
    season: str,
    home_id: uuid.UUID,
    away_id: uuid.UUID,
) -> uuid.UUID | None:
    stmt = (
        pg_insert(Fixture)
        .values(
            id=uuid.uuid4(),
            league_id=league_id,
            season=season,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=dto.kickoff_at,
            kickoff_time_known=dto.kickoff_time_known,
            status=FixtureStatus(dto.status),
            ft_home=dto.ft_home,
            ft_away=dto.ft_away,
            ht_home=dto.ht_home,
            ht_away=dto.ht_away,
            source=dto.provider,
        )
        .on_conflict_do_nothing(constraint="uq_fixture_identity")
        .returning(Fixture.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _insert_odds(session: AsyncSession, dto: FixtureDTO, fixture_id: uuid.UUID) -> int:
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
        inserted += len((await session.execute(stmt)).fetchall())
    return inserted


async def ingest_from_adapter(
    session: AsyncSession,
    adapter: SourceAdapter,
    *,
    league_code: str,
    season: str,
    meta: LeagueMeta | None = None,
) -> IngestSummary:
    """Licence-gate, fetch and ingest one ``(league, season)`` from ``adapter``."""
    ensure_licensed(adapter)
    records = await adapter.fetch(league_code, season)
    return await ingest_records(
        session,
        records,
        provider=adapter.name,
        league_code=league_code,
        seed=adapter.may_seed_canonical,
        meta=meta,
    )
