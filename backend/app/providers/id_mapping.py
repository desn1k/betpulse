"""ID-mapping layer.

Canonical internal ``team_id`` / ``league_id`` with per-provider alias tables so
the same entity from two sources resolves to one row. Cross-source fixture
dedup (``app.services.ingestion.core``) compares these canonical ids, so a
team must resolve to the same row from every source.

Two access modes:
- ``resolve_team`` / ``resolve_league`` — strict: raise :class:`UnmappedEntityError`
  on an unknown name. Used by non-seed providers; an unmapped entity is never
  silently ignored or duplicated. An unmapped team is also recorded in
  ``provider_unmapped_teams`` (the worklist behind ``app.cli unmapped-teams``).
  A provider's stable team id (``external_id``) is tried before the name, so a
  renamed club still resolves.
- ``get_or_create_canonical_*`` — used only by a **seed** source, which may
  create a canonical entity the first time it is seen. Canonical teams are
  unique per ``(country, normalized_name)``: the same name in two countries is
  two clubs. Callers log a structured warning so creation is visible.

``map_team`` binds a provider's name (and optionally its id) to a canonical
team by hand — audited — and clears it from the unmapped worklist.
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reference import (
    League,
    ProviderLeagueAlias,
    ProviderTeamAlias,
    ProviderUnmappedTeam,
    Team,
)
from app.services.audit import record_event

TEAM_MAPPED = "ingestion.team.mapped"


class UnmappedEntityError(Exception):
    """Raised when a provider name cannot be resolved to a canonical entity."""


def normalize_name(name: str) -> str:
    """Fold accents, lowercase, strip punctuation, collapse whitespace."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return folded.strip()


# --- Strict resolvers (non-seed providers) ---------------------------------


async def _record_unmapped(
    session: AsyncSession,
    *,
    provider: str,
    raw_name: str,
    external_id: str | None,
    league_hint: str | None,
) -> None:
    stmt = pg_insert(ProviderUnmappedTeam).values(
        id=uuid.uuid4(),
        provider=provider,
        raw_name=raw_name,
        external_id=external_id,
        league_hint=league_hint,
        seen_count=1,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_unmapped_team",
            set_={
                "seen_count": ProviderUnmappedTeam.seen_count + 1,
                "last_seen_at": func.now(),
                "external_id": func.coalesce(
                    stmt.excluded.external_id, ProviderUnmappedTeam.external_id
                ),
                "league_hint": func.coalesce(
                    stmt.excluded.league_hint, ProviderUnmappedTeam.league_hint
                ),
            },
        )
    )


async def resolve_team(
    session: AsyncSession,
    provider: str,
    raw_name: str,
    *,
    external_id: str | None = None,
    league_hint: str | None = None,
) -> Team:
    """Strictly resolve a provider's team: by its stable id first, then by name."""
    alias: ProviderTeamAlias | None = None
    if external_id is not None:
        alias = (
            await session.execute(
                select(ProviderTeamAlias).where(
                    ProviderTeamAlias.provider == provider,
                    ProviderTeamAlias.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
    if alias is None:
        alias = (
            await session.execute(
                select(ProviderTeamAlias).where(
                    ProviderTeamAlias.provider == provider,
                    ProviderTeamAlias.alias == raw_name,
                )
            )
        ).scalar_one_or_none()
    if alias is None:
        await _record_unmapped(
            session,
            provider=provider,
            raw_name=raw_name,
            external_id=external_id,
            league_hint=league_hint,
        )
        raise UnmappedEntityError(f"team '{raw_name}' from provider '{provider}' is unmapped")
    if external_id is not None and alias.external_id is None:
        alias.external_id = external_id  # learn the stable id on first sight
    team = await session.get(Team, alias.team_id)
    if team is None:  # pragma: no cover - referential integrity guarantees this
        raise UnmappedEntityError(f"team alias '{raw_name}' points at a missing team")
    return team


async def resolve_league(session: AsyncSession, provider: str, raw_code: str) -> League:
    alias = (
        await session.execute(
            select(ProviderLeagueAlias).where(
                ProviderLeagueAlias.provider == provider,
                ProviderLeagueAlias.alias == raw_code,
            )
        )
    ).scalar_one_or_none()
    if alias is None:
        raise UnmappedEntityError(f"league '{raw_code}' from provider '{provider}' is unmapped")
    league = await session.get(League, alias.league_id)
    if league is None:  # pragma: no cover
        raise UnmappedEntityError(f"league alias '{raw_code}' points at a missing league")
    return league


# --- Seed helpers (canonical source only) ----------------------------------


async def get_or_create_canonical_team(
    session: AsyncSession,
    *,
    provider: str,
    raw_name: str,
    country: str | None = None,
    external_id: str | None = None,
) -> tuple[Team, bool]:
    """Resolve or create a canonical team by ``(country, normalized name)``;
    ensure the alias. Returns ``(team, created)``. ``created`` is True only when
    a brand-new canonical team row was inserted (caller should log it)."""
    normalized = normalize_name(raw_name)
    team = (
        await session.execute(
            select(Team).where(
                Team.normalized_name == normalized, Team.country.is_not_distinct_from(country)
            )
        )
    ).scalar_one_or_none()

    created = False
    if team is None:
        team = Team(name=raw_name.strip(), normalized_name=normalized, country=country)
        session.add(team)
        await session.flush()
        created = True

    await _ensure_team_alias(
        session, provider=provider, raw_name=raw_name, team=team, external_id=external_id
    )
    return team, created


async def _ensure_team_alias(
    session: AsyncSession,
    *,
    provider: str,
    raw_name: str,
    team: Team,
    external_id: str | None = None,
) -> None:
    alias = (
        await session.execute(
            select(ProviderTeamAlias).where(
                ProviderTeamAlias.provider == provider,
                ProviderTeamAlias.alias == raw_name,
            )
        )
    ).scalar_one_or_none()
    if alias is None:
        session.add(
            ProviderTeamAlias(
                provider=provider, alias=raw_name, team_id=team.id, external_id=external_id
            )
        )
        await session.flush()
    elif external_id is not None and alias.external_id is None:
        alias.external_id = external_id
        await session.flush()


async def get_or_create_canonical_league(
    session: AsyncSession,
    *,
    provider: str,
    code: str,
    name: str,
    raw_code: str,
    country: str | None = None,
    season_start_month: int | None = None,
) -> tuple[League, bool]:
    league = (await session.execute(select(League).where(League.code == code))).scalar_one_or_none()

    created = False
    if league is None:
        league = League(
            code=code, name=name, country=country, season_start_month=season_start_month
        )
        session.add(league)
        await session.flush()
        created = True

    exists = (
        await session.execute(
            select(ProviderLeagueAlias.id).where(
                ProviderLeagueAlias.provider == provider,
                ProviderLeagueAlias.alias == raw_code,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(ProviderLeagueAlias(provider=provider, alias=raw_code, league_id=league.id))
        await session.flush()
    return league, created


# --- Manual mapping ----------------------------------------------------------


async def map_team(
    session: AsyncSession,
    *,
    provider: str,
    raw_name: str,
    team_id: uuid.UUID,
    external_id: str | None = None,
    actor: str = "cli",
) -> ProviderTeamAlias:
    """Bind ``provider``'s ``raw_name`` (and optional stable id) to a canonical
    team, clear it from the unmapped worklist, and audit the change. Re-mapping
    an existing alias to another team is allowed and audited with both ids."""
    team = await session.get(Team, team_id)
    if team is None:
        raise UnmappedEntityError(f"canonical team {team_id} does not exist")
    alias = (
        await session.execute(
            select(ProviderTeamAlias).where(
                ProviderTeamAlias.provider == provider, ProviderTeamAlias.alias == raw_name
            )
        )
    ).scalar_one_or_none()
    previous = None if alias is None else alias.team_id
    if alias is None:
        alias = ProviderTeamAlias(
            provider=provider, alias=raw_name, team_id=team.id, external_id=external_id
        )
        session.add(alias)
    else:
        alias.team_id = team.id
        if external_id is not None:
            alias.external_id = external_id
    await session.flush()

    unmapped = (
        await session.execute(
            select(ProviderUnmappedTeam).where(
                ProviderUnmappedTeam.provider == provider,
                ProviderUnmappedTeam.raw_name == raw_name,
            )
        )
    ).scalar_one_or_none()
    if unmapped is not None:
        await session.delete(unmapped)

    await record_event(
        session,
        action=TEAM_MAPPED,
        target=f"{provider}:{raw_name}",
        meta={
            "actor": actor,
            "team_id": str(team.id),
            "previous_team_id": None if previous is None else str(previous),
            "external_id": external_id,
        },
    )
    await session.flush()
    return alias


async def unmapped_teams(
    session: AsyncSession, *, provider: str | None = None
) -> list[ProviderUnmappedTeam]:
    stmt = select(ProviderUnmappedTeam).order_by(
        ProviderUnmappedTeam.provider, ProviderUnmappedTeam.seen_count.desc()
    )
    if provider is not None:
        stmt = stmt.where(ProviderUnmappedTeam.provider == provider)
    return list((await session.execute(stmt)).scalars().all())
