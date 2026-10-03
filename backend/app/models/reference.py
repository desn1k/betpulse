"""Reference entities: leagues, teams, provider ID-mapping and accounts."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ProviderRole(enum.StrEnum):
    historical = "historical"
    live = "live"
    odds = "odds"
    xg = "xg"


class League(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "leagues"

    # Canonical internal league code, e.g. EPL, LALIGA, UCL, RPL.
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Month a season starts (8 = August for the top-5 leagues, 7 = July for the
    # RPL) for leagues whose season spans two calendar years: canonical season
    # labels are then ``YYYY-YYYY``. NULL = calendar-year league, ``YYYY``
    # (see app.core.seasons).
    season_start_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        # Canonical dedup key: the same normalized name in two countries is two
        # clubs. NULL countries compare equal so they cannot slip past it.
        UniqueConstraint(
            "country",
            "normalized_name",
            name="uq_team_country_name",
            postgresql_nulls_not_distinct=True,
        ),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Providers resolve to a team via the alias tables, never by this name.
    normalized_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProviderTeamAlias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_team_aliases"
    __table_args__ = (
        UniqueConstraint("provider", "alias", name="uq_team_alias"),
        # A provider's stable team id beats its (renamable) display name.
        UniqueConstraint("provider", "external_id", name="uq_team_alias_external"),
    )

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
    )


class ProviderUnmappedTeam(UUIDPrimaryKeyMixin, Base):
    """A team name (or id) a strict provider sent that no alias resolves yet.

    Filled by the strict resolver, listed by ``app.cli unmapped-teams`` and
    cleared by ``app.cli map-team`` — so unmapped entities are a worklist,
    not only a log line.
    """

    __tablename__ = "provider_unmapped_teams"
    __table_args__ = (UniqueConstraint("provider", "raw_name", name="uq_unmapped_team"),)

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    raw_name: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    league_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderLeagueAlias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_league_aliases"
    __table_args__ = (UniqueConstraint("provider", "alias", name="uq_league_alias"),)

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    league_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), index=True, nullable=False
    )


class ProviderAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_accounts"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # Roles this account fulfils (values of ProviderRole).
    roles: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # API key encrypted at rest with the same Fernet/DATA_ENCRYPTION_KEY as
    # every other stored secret (app.core.crypto). Only a masked suffix is
    # ever returned to a client.
    encrypted_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    key_suffix: Mapped[str | None] = mapped_column(String(8), nullable=True)

    requests_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requests_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
