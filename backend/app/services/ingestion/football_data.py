"""football-data.co.uk historical ingestion — **dev / local only**.

football-data.co.uk carries no licence for commercial use, so its adapter is
``licensed_for_production = False`` and the provider-agnostic core
(:mod:`app.services.ingestion.core`) refuses it in the ``production``
environment. It remains the canonical *seed* source for development data: it
may create canonical leagues/teams the first time it sees them, each creation
logged as structured JSON so it is actionable, never silent.

Idempotent: every row carries a synthetic external id
(``fd:{division}:{season}:{YYYYMMDD}:{home}:{away}``), so a re-run finds the
existing fixtures instead of inserting.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.dtos import FixtureDTO
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.services.ingestion.core import (
    IngestSummary,
    LeagueMeta,
    ensure_licensed,
    ingest_records,
)

# Canonical league code -> metadata. Only leagues football-data actually covers;
# RPL is intentionally absent (see ingest warning + docs). All five start their
# season in August (split-year labels ``YYYY-YYYY``).
LEAGUE_META: dict[str, LeagueMeta] = {
    "EPL": LeagueMeta("Premier League", "England", 8),
    "LALIGA": LeagueMeta("La Liga", "Spain", 8),
    "SERIEA": LeagueMeta("Serie A", "Italy", 8),
    "BUNDESLIGA": LeagueMeta("Bundesliga", "Germany", 8),
    "LIGUE1": LeagueMeta("Ligue 1", "France", 8),
}

PROVIDER = FootballDataCoUkProvider.name

__all__ = ["LEAGUE_META", "PROVIDER", "IngestSummary", "ingest_dtos"]


async def ingest_dtos(
    session: AsyncSession, dtos: list[FixtureDTO], *, league_code: str
) -> IngestSummary:
    """Ingest parsed football-data rows for one league (licence-gated)."""
    ensure_licensed(FootballDataCoUkProvider)
    return await ingest_records(
        session,
        dtos,
        provider=PROVIDER,
        league_code=league_code,
        seed=FootballDataCoUkProvider.may_seed_canonical,
        meta=LEAGUE_META[league_code],
    )
