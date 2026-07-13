"""Data CLI.

Usage:
    python -m app.cli bootstrap-history [--leagues EPL,LALIGA] [--seasons 2023-2024]
                                        [--offline-dir DIR]
    python -m app.cli verify-history   [--leagues ...] [--seasons ...]

``--offline-dir`` reads committed CSV fixtures instead of downloading — used by
CI. Without it, CSVs are fetched from football-data.co.uk (local dev / VPS).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.db import _write_sessionmaker
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.services.ingestion.football_data import LEAGUE_META
from app.services.ingestion.runner import (
    VerifyRow,
    bootstrap_history,
    network_csv_source,
    offline_csv_source,
    verify_history,
)

DEFAULT_LEAGUES = list(LEAGUE_META)
DEFAULT_SEASONS = ["2023-2024"]


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


async def _bootstrap(leagues: list[str], seasons: list[str], offline_dir: str | None) -> int:
    provider = FootballDataCoUkProvider(leagues=leagues)
    csv_source = (
        offline_csv_source(Path(offline_dir)) if offline_dir else network_csv_source(provider)
    )
    async with _write_sessionmaker()() as session:
        summary = await bootstrap_history(
            session, leagues=leagues, seasons=seasons, csv_source=csv_source, provider=provider
        )
        await session.commit()
    print(
        f"bootstrap-history: fixtures +{summary.fixtures_inserted}/{summary.fixtures_seen}, "
        f"odds +{summary.odds_inserted}, teams +{summary.teams_created}, "
        f"leagues +{summary.leagues_created}"
    )
    return 0


def _print_table(rows: list[VerifyRow]) -> None:
    print(f"{'league':<12} {'season':<10} {'fixtures':>9} {'odds':>7}")
    print("-" * 42)
    for r in rows:
        print(f"{r.league:<12} {r.season:<10} {r.fixture_count:>9} {r.odds_count:>7}")


async def _verify(leagues: list[str], seasons: list[str]) -> int:
    async with _write_sessionmaker()() as session:
        rows, ok = await verify_history(session, leagues=leagues, seasons=seasons)
    _print_table(rows)
    if not ok:
        gaps = [f"{r.league} {r.season}" for r in rows if r.fixture_count == 0]
        print(f"\nFAIL: no fixtures for: {', '.join(gaps)}", file=sys.stderr)
        return 1
    print("\nOK: every configured league/season has fixtures.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("bootstrap-history", "verify-history"):
        p = sub.add_parser(name)
        p.add_argument("--leagues", type=_split, default=DEFAULT_LEAGUES)
        p.add_argument("--seasons", type=_split, default=DEFAULT_SEASONS)
        if name == "bootstrap-history":
            p.add_argument("--offline-dir", default=None)

    args = parser.parse_args(argv)
    if args.command == "bootstrap-history":
        return asyncio.run(_bootstrap(args.leagues, args.seasons, args.offline_dir))
    if args.command == "verify-history":
        return asyncio.run(_verify(args.leagues, args.seasons))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
