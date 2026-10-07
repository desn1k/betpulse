"""Alembic async migration environment."""

from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig
from typing import Any

from alembic import context
from app.core.config import get_settings

# Import models so every table is registered on the shared metadata.
from app.core.db import Base
from app.models import (  # noqa: F401
    AuditLog,
    EmailVerificationToken,
    RefreshToken,
    User,
)
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime database URL (never hardcoded in alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata

# Postgres messages a migration raises (RAISE NOTICE / WARNING, extension
# notices). asyncpg drops them unless a listener is registered; forward them to
# the alembic logger, which alembic.ini prints to stderr at INFO. stdout (what
# `alembic heads` / `current` print) is untouched.
_server_log = logging.getLogger("alembic")


def _log_server_message(_connection: Any, message: Any) -> None:
    severity = str(getattr(message, "severity", "NOTICE"))
    level = logging.WARNING if severity.upper() == "WARNING" else logging.INFO
    _server_log.log(level, "postgres %s: %s", severity, message.message)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    # Online mode only: offline (--sql) never connects.
    connection.connection.driver_connection.add_log_listener(_log_server_message)
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
