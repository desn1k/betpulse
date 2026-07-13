"""ID-mapping layer.

Canonical internal ``team_id`` / ``league_id`` with per-provider alias tables so
the same entity from two sources resolves to one row.

Two access modes:
- ``resolve_team`` / ``resolve_league`` — strict: raise :class:`UnmappedEntityError`
  on an unknown name. Used by non-seed providers (e.g. API-Football); an unmapped
  entity is never silently ignored or duplicated.
- ``get_or_create_canonical_*`` — used only by the canonical **seed** source
  (football-data.co.uk), which is allowed to create a new canonical entity the
  first time it is seen. Callers log a structured warning so the creation is
  visible and actionable.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reference import (
    League,
    ProviderLeagueAlias,
    ProviderTeamAlias,
    Team,
)


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


async def resolve_team(session: AsyncSession, provider: str, raw_name: str) -> Team:
    alias = (
        await session.execute(
            select(ProviderTeamAlias).where(
                ProviderTeamAlias.provider == provider,
                ProviderTeamAlias.alias == raw_name,
            )
        )
    ).scalar_one_or_none()
    if alias is None:
        raise UnmappedEntityError(f"team '{raw_name}' from provider '{provider}' is unmapped")
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
    session: AsyncSession, *, provider: str, raw_name: str, country: str | None = None
) -> tuple[Team, bool]:
    """Resolve or create a canonical team by normalized name; ensure the alias.

    Returns ``(team, created)``. ``created`` is True only when a brand-new
    canonical team row was inserted (caller should log it).
    """
    normalized = normalize_name(raw_name)
    team = (
        await session.execute(select(Team).where(Team.normalized_name == normalized))
    ).scalar_one_or_none()

    created = False
    if team is None:
        team = Team(name=raw_name.strip(), normalized_name=normalized, country=country)
        session.add(team)
        await session.flush()
        created = True

    await _ensure_team_alias(session, provider=provider, raw_name=raw_name, team=team)
    return team, created


async def _ensure_team_alias(
    session: AsyncSession, *, provider: str, raw_name: str, team: Team
) -> None:
    exists = (
        await session.execute(
            select(ProviderTeamAlias.id).where(
                ProviderTeamAlias.provider == provider,
                ProviderTeamAlias.alias == raw_name,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(ProviderTeamAlias(provider=provider, alias=raw_name, team_id=team.id))
        await session.flush()


async def get_or_create_canonical_league(
    session: AsyncSession,
    *,
    provider: str,
    code: str,
    name: str,
    raw_code: str,
    country: str | None = None,
) -> tuple[League, bool]:
    league = (await session.execute(select(League).where(League.code == code))).scalar_one_or_none()

    created = False
    if league is None:
        league = League(code=code, name=name, country=country)
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
