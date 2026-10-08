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
    python -m app.cli reset-2fa        --email ADDRESS [--require-password-change]

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
from app.core.outbound import install_log_safety
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.services.ingestion.football_data import LEAGUE_META
from app.services.ingestion.runner import (
    VerifyRow,
    network_csv_source,
    offline_csv_source,
    run_recorded_ingestion,
    verify_history,
)

DEFAULT_LEAGUES = list(LEAGUE_META)
DEFAULT_SEASONS = ["2023-2024"]


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


async def _bootstrap(
    leagues: list[str], seasons: list[str], offline_dir: str | None, force: bool = False
) -> int:
    """Per-pair committed, resumable ingestion (one ``ingestion_runs`` row per
    league/season; unchanged payloads are skipped unless ``--force``)."""
    provider = FootballDataCoUkProvider(leagues=leagues)
    csv_source = (
        offline_csv_source(Path(offline_dir)) if offline_dir else network_csv_source(provider)
    )
    from app.services.ingestion.core import SourceNotLicensed

    try:
        async with _write_sessionmaker()() as session:
            runs = await run_recorded_ingestion(
                session,
                leagues=leagues,
                seasons=seasons,
                csv_source=csv_source,
                provider_name=provider.name,
                triggered_by="cli",
                force=force,
                provider=provider,
            )
    except SourceNotLicensed as exc:
        print(f"bootstrap-history: {exc}", file=sys.stderr)
        return 2
    failed = 0
    for r in runs:
        note = f" ({r.skipped_reason})" if r.skipped_reason else ""
        print(
            f"bootstrap-history: {r.league} {r.season}: {r.status.value}{note}, "
            f"fixtures +{r.fixtures_ingested}, odds +{r.odds_ingested}"
            + (f", error: {r.error}" if r.error else "")
        )
        failed += r.status.value == "failed"
    return 1 if failed else 0


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


async def _reset_2fa(email: str, *, require_password_change: bool) -> int:
    """Operator path for a lost authenticator (needs shell access to the server).

    Turns TOTP off and forgets the secret, revokes every refresh token of the
    account (all its sessions end within one access-token lifetime), optionally
    forces a password change at the next sign-in, and audits the reset with no
    actor. Prints one line and never the secret. An admin then signs in with the
    password alone and is led to set up TOTP again.
    """
    from sqlalchemy import select

    from app.models.user import User
    from app.services.audit import AuditAction, record_event
    from app.services.auth import mark_credentials_changed, revoke_all_user_tokens

    normalized = email.strip().lower()
    async with _write_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.email == normalized))
        if user is None:
            print("reset-2fa: no user with that email; nothing changed")
            return 1
        was_enabled = user.totp_enabled
        user.totp_enabled = False
        user.totp_secret_encrypted = None
        if require_password_change:
            user.must_change_password = True
        # Live access tokens die too, not only the refresh tokens (ER2-01).
        changed_at = mark_credentials_changed(user)
        revoked = await revoke_all_user_tokens(session, user.id)
        await record_event(
            session,
            action=AuditAction.TWOFA_RESET_BY_OPERATOR,
            target=user.email,
            meta={
                "was_enabled": was_enabled,
                "refresh_tokens_revoked": revoked,
                "require_password_change": require_password_change,
                "credentials_changed_at": changed_at.isoformat(),
            },
        )
        await session.commit()
    print(
        f"reset-2fa: {normalized}: two-factor authentication turned off "
        f"(was {'on' if was_enabled else 'off'}), {revoked} refresh tokens revoked, "
        f"password change required: {'yes' if require_password_change else 'no'}"
    )
    return 0


def _provider_record(args: argparse.Namespace) -> int:
    from app.providers.recording import ProviderKeys, load_manifest, plan_text, record

    manifest = Path(args.manifest)
    calls = load_manifest(manifest)
    print(plan_text(args.provider, calls))
    if not args.execute:
        print("dry run: no request made (add --execute)")
        return 0
    keys = ProviderKeys(_env_file=args.env_file)
    _, completed = asyncio.run(
        record(
            args.provider,
            calls,
            key=keys.for_provider(args.provider),
            out_dir=Path(args.out_dir) if args.out_dir else manifest.parent,
            max_credits=(
                args.max_credits if args.max_credits is not None else sum(c.credits for c in calls)
            ),
        )
    )
    return 0 if completed else 1


def main(argv: list[str] | None = None) -> int:
    install_log_safety()
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("bootstrap-history", "verify-history"):
        p = sub.add_parser(name)
        p.add_argument("--leagues", type=_split, default=DEFAULT_LEAGUES)
        p.add_argument("--seasons", type=_split, default=DEFAULT_SEASONS)
        if name == "bootstrap-history":
            p.add_argument("--offline-dir", default=None)
            p.add_argument("--force", action="store_true", help="re-ingest even unchanged payloads")
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
    reset = sub.add_parser(
        "reset-2fa", help="lost authenticator: turn TOTP off and end every session"
    )
    reset.add_argument("--email", required=True)
    reset.add_argument(
        "--require-password-change",
        action="store_true",
        help="also force a password change at the next sign-in",
    )
    rec = sub.add_parser("provider-record", help="record real provider responses as fixtures")
    rec.add_argument("--provider", required=True, choices=["sportmonks", "the_odds_api"])
    rec.add_argument("--manifest", required=True, help="JSON list of calls")
    rec.add_argument("--out-dir", default=None, help="default: the manifest's directory")
    rec.add_argument("--env-file", default=".env", help="where the provider keys are read from")
    rec.add_argument("--max-credits", type=int, default=None, help="default: manifest total")
    rec.add_argument("--execute", action="store_true", help="make the calls (default: dry run)")

    args = parser.parse_args(argv)
    if args.command == "bootstrap-history":
        return asyncio.run(_bootstrap(args.leagues, args.seasons, args.offline_dir, args.force))
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
    if args.command == "reset-2fa":
        return asyncio.run(
            _reset_2fa(args.email, require_password_change=args.require_password_change)
        )
    if args.command == "train":
        return asyncio.run(_train())
    if args.command == "provider-record":
        return _provider_record(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
