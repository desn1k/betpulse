"""Reference entities: leagues, teams, provider ID-mapping and accounts."""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
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


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Canonical dedup key; providers resolve to this via the alias tables.
    normalized_name: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProviderTeamAlias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_team_aliases"
    __table_args__ = (UniqueConstraint("provider", "alias", name="uq_team_alias"),)

    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
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
