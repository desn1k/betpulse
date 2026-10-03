"""Live ingestion: poll the live provider and upsert in-play fixture state.

Idempotent and quota-safe: the provider's remaining quota is checked *before*
the request (hard stop, never overspend). Each live fixture is resolved
**strictly** through the ID-mapping aliases; an unmapped team/league is never
guessed — it is logged as a structured warning and the fixture is skipped
(alias seeding is an admin task). Re-polling the same state changes nothing new;
downstream recompute is what decides whether to act on a change.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seasons import SeasonFormatError
from app.models.fixture import Fixture, FixtureStatus
from app.providers.base import BaseProvider, ProviderQuotaExhausted
from app.providers.dtos import LiveFixtureDTO
from app.providers.id_mapping import (
    UnmappedEntityError,
    resolve_league,
    resolve_team,
)
from app.services.ingestion.core import canonical_season_for, find_fixture, link_external_ref

logger = logging.getLogger("live.ingestion")


@dataclass(slots=True)
class LiveFixtureState:
    """Resolved, persisted in-play state for one fixture."""

    fixture_id: uuid.UUID
    minute: int
    home_score: int
    away_score: int


@dataclass(slots=True)
class LivePollResult:
    seen: int = 0
    ingested: int = 0
    skipped_unmapped: int = 0
    states: list[LiveFixtureState] = field(default_factory=list)


def _warn_unmapped(dto: LiveFixtureDTO, reason: str) -> None:
    logger.warning(
        json.dumps(
            {
                "event": "live_fixture_unmapped",
                "reason": reason,
                "provider": dto.provider,
                "provider_fixture_id": dto.provider_fixture_id,
                "league_raw_code": dto.league.raw_code,
                "league_raw_name": dto.league.raw_name,
                "home": dto.home.raw_name,
                "away": dto.away.raw_name,
            }
        )
    )


async def poll_live(
    session: AsyncSession, provider: BaseProvider, *, dtos: list[LiveFixtureDTO] | None = None
) -> LivePollResult:
    """Fetch live fixtures (quota-guarded) and upsert their in-play state.

    ``dtos`` may be supplied to bypass the network (used by tests against a
    recorded cassette); otherwise the provider is polled.
    """
    if dtos is None:
        quota = await provider.rate_limit_state()
        if quota.requests_remaining <= 0:
            raise ProviderQuotaExhausted(
                f"provider '{provider.name}' has no remaining quota "
                f"(resets_at={quota.resets_at}); refusing to poll live"
            )
        dtos = await provider.fetch_live()

    result = LivePollResult()
    for dto in dtos:
        result.seen += 1
        league_code = dto.league.raw_code or ""
        try:
            league = await resolve_league(session, dto.provider, league_code)
            home = await resolve_team(
                session,
                dto.provider,
                dto.home.raw_name,
                external_id=dto.home.external_id,
                league_hint=league_code,
            )
            away = await resolve_team(
                session,
                dto.provider,
                dto.away.raw_name,
                external_id=dto.away.external_id,
                league_hint=league_code,
            )
            season = canonical_season_for(league, dto.season)
        except (UnmappedEntityError, SeasonFormatError) as exc:
            result.skipped_unmapped += 1
            _warn_unmapped(dto, str(exc))
            continue

        fixture_id = await _upsert_live_fixture(
            session, dto, season=season, league_id=league.id, home_id=home.id, away_id=away.id
        )
        result.ingested += 1
        result.states.append(
            LiveFixtureState(
                fixture_id=fixture_id,
                minute=dto.minute,
                home_score=dto.home_score,
                away_score=dto.away_score,
            )
        )

    await session.flush()
    return result


async def _upsert_live_fixture(
    session: AsyncSession,
    dto: LiveFixtureDTO,
    *,
    season: str,
    league_id: uuid.UUID,
    home_id: uuid.UUID,
    away_id: uuid.UUID,
) -> uuid.UUID:
    """Create the fixture on first sight, or advance its live minute/status.

    Identity goes through the provider-agnostic core: the provider's own
    fixture id (``fixture_external_refs``) first, then the cross-source dedup
    (same league and teams within ±36 h, any season label) — so a match that is
    already present from another source is updated in place, not duplicated.
    """
    existing, _ = await find_fixture(
        session,
        provider=dto.provider,
        external_id=dto.provider_fixture_id,
        league_id=league_id,
        home_id=home_id,
        away_id=away_id,
        kickoff=dto.kickoff_at,
    )
    if existing is not None:
        existing.status = FixtureStatus.live
        existing.minute = dto.minute
        existing.last_polled_at = func.now()
        await session.flush()
        await link_external_ref(
            session,
            fixture_id=existing.id,
            provider=dto.provider,
            external_id=dto.provider_fixture_id,
        )
        return existing.id

    stmt = (
        pg_insert(Fixture)
        .values(
            id=uuid.uuid4(),
            league_id=league_id,
            season=season,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=dto.kickoff_at,
            status=FixtureStatus.live,
            minute=dto.minute,
            source=dto.provider,
            last_polled_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="uq_fixture_identity",
            set_={
                "status": FixtureStatus.live,
                "minute": dto.minute,
                "last_polled_at": func.now(),
            },
        )
        .returning(Fixture.id)
    )
    fixture_id: uuid.UUID = (await session.execute(stmt)).scalar_one()
    await link_external_ref(
        session, fixture_id=fixture_id, provider=dto.provider, external_id=dto.provider_fixture_id
    )
    return fixture_id
