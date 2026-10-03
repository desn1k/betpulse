"""Data CLI.

Usage:
    python -m app.cli bootstrap-history [--leagues EPL,LALIGA] [--seasons 2023-2024]
                                        [--offline-dir DIR]
    python -m app.cli verify-history   [--leagues ...] [--seasons ...]
    python -m app.cli data-report      [--leagues ...] [--seasons ...] [--json] [--strict]
    python -m app.cli unmapped-teams   [--provider NAME]
    python -m app.cli map-team         --provider NAME --alias "Raw Name"
                                       (--team-id UUID | --team NAME [--country C])
                                       [--external-id ID]

``--offline-dir`` reads committed CSV fixtures instead of downloading — used by
CI. Without it, CSVs are fetched from football-data.co.uk (local dev / VPS).
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    from app.services.ingestion.core import SourceNotLicensed

    try:
        async with _write_sessionmaker()() as session:
            summary = await bootstrap_history(
                session, leagues=leagues, seasons=seasons, csv_source=csv_source, provider=provider
            )
            await session.commit()
    except SourceNotLicensed as exc:
        print(f"bootstrap-history: {exc}", file=sys.stderr)
        return 2
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


async def _data_report(
    leagues: list[str] | None, seasons: list[str] | None, as_json: bool, strict: bool
) -> int:
    """Read-only coverage + quality report (safe on production)."""
    from app.core.db import _read_sessionmaker
    from app.services.data_quality import build_report, format_report

    async with _read_sessionmaker()() as session:
        report = await build_report(session, leagues=leagues, seasons=seasons)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return report.exit_code(strict=strict)


async def _unmapped_teams(provider: str | None) -> int:
    from app.providers.id_mapping import unmapped_teams

    async with _write_sessionmaker()() as session:
        rows = await unmapped_teams(session, provider=provider)
    if not rows:
        print("no unmapped teams")
        return 0
    print("provider | raw_name | external_id | league | seen | last_seen")
    for r in rows:
        print(
            f"{r.provider} | {r.raw_name} | {r.external_id or '-'} | {r.league_hint or '-'} | "
            f"{r.seen_count} | {r.last_seen_at.isoformat()}"
        )
    return 0


async def _map_team(
    provider: str,
    alias: str,
    team_id: str | None,
    team_name: str | None,
    country: str | None,
    external_id: str | None,
) -> int:
    import uuid

    from sqlalchemy import select

    from app.models.reference import Team
    from app.providers.id_mapping import UnmappedEntityError, map_team, normalize_name

    async with _write_sessionmaker()() as session:
        if team_id is not None:
            target = uuid.UUID(team_id)
        else:
            matches = (
                (
                    await session.execute(
                        select(Team).where(
                            Team.normalized_name == normalize_name(team_name or ""),
                            *([Team.country == country] if country else []),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(matches) != 1:
                found = ", ".join(f"{t.id} ({t.country})" for t in matches) or "none"
                print(
                    f"map-team: need exactly one canonical team for {team_name!r}; "
                    f"found {found}. Pass --team-id or --country.",
                    file=sys.stderr,
                )
                return 2
            target = matches[0].id
        try:
            await map_team(
                session,
                provider=provider,
                raw_name=alias,
                team_id=target,
                external_id=external_id,
            )
        except UnmappedEntityError as exc:
            print(f"map-team: {exc}", file=sys.stderr)
            return 2
        await session.commit()
    print(f"mapped {provider}:{alias!r} -> {target}")
    return 0


async def _train() -> int:
    from app.ml.training import run_training

    async with _write_sessionmaker()() as session:
        summary = await run_training(session)
        await session.commit()
    print(
        f"train: version={summary.version} trained={summary.trained} "
        f"predictions={summary.predictions_written} skipped={summary.skipped}"
    )
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
    report = sub.add_parser("data-report", help="read-only coverage + data-quality report")
    report.add_argument("--leagues", type=_split, default=None)
    report.add_argument("--seasons", type=_split, default=None)
    report.add_argument("--json", action="store_true", help="machine-readable output")
    report.add_argument("--strict", action="store_true", help="warnings also fail")
    unmapped = sub.add_parser("unmapped-teams", help="team names no alias resolves yet")
    unmapped.add_argument("--provider", default=None)
    mapping = sub.add_parser("map-team", help="bind a provider team name to a canonical team")
    mapping.add_argument("--provider", required=True)
    mapping.add_argument("--alias", required=True, help="the provider's raw team name")
    target = mapping.add_mutually_exclusive_group(required=True)
    target.add_argument("--team-id", default=None)
    target.add_argument("--team", default=None, help="canonical team name")
    mapping.add_argument("--country", default=None)
    mapping.add_argument("--external-id", default=None, help="the provider's stable team id")
    sub.add_parser("train")

    args = parser.parse_args(argv)
    if args.command == "bootstrap-history":
        return asyncio.run(_bootstrap(args.leagues, args.seasons, args.offline_dir))
    if args.command == "verify-history":
        return asyncio.run(_verify(args.leagues, args.seasons))
    if args.command == "data-report":
        return asyncio.run(_data_report(args.leagues, args.seasons, args.json, args.strict))
    if args.command == "unmapped-teams":
        return asyncio.run(_unmapped_teams(args.provider))
    if args.command == "map-team":
        return asyncio.run(
            _map_team(
                args.provider, args.alias, args.team_id, args.team, args.country, args.external_id
            )
        )
    if args.command == "train":
        return asyncio.run(_train())
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
