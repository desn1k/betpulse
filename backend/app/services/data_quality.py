"""Read-only data-quality report over the historical data plane.

``build_report`` scans fixtures and odds (optionally scoped to leagues/seasons)
and returns per (league, season) coverage rows plus a list of issues. Nothing is
written, so it is safe to run against production — e.g. before a migration, to
see conflicts upfront.

Errors are data that would corrupt training or evaluation (duplicates across
sources, missing or impossible scores, unusable or mislabelled odds); warnings
are gaps worth a look (coverage, odd team counts, non-canonical season labels).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.fixture import Fixture, FixtureStatus
from app.models.ingestion_run import IngestionConflict
from app.models.market import Odds
from app.models.reference import League
from app.providers.dtos import MAX_PRICE, MIN_PRICE

ERROR = "error"
WARNING = "warning"

# Same pairing in the same league this close in time is one match recorded twice.
DUPLICATE_WINDOW = timedelta(hours=36)
MAX_GOALS = 15
OVERROUND_RANGE = (1.0, 1.25)
TYPICAL_TEAM_COUNTS = {16, 18, 20}
MIN_ODDS_COVERAGE = 0.9
# A season with no fixture in this many days is over; only then are missing
# fixtures or uneven team schedules reported.
SEASON_OVER_AFTER = timedelta(days=60)
MARKET_OUTCOMES = {"1x2": 3, "ou_2.5": 2}
_SEASON_SPLIT = re.compile(r"^(\d{4})-(\d{4})$")
_SEASON_CALENDAR = re.compile(r"^\d{4}$")


@dataclass(slots=True)
class Issue:
    severity: str
    code: str
    league: str
    season: str
    detail: str
    fixture_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CoverageRow:
    league: str
    season: str
    fixtures: int
    finished: int
    with_score: int
    teams: int
    expected_fixtures: int
    time_known: int
    closing_1x2_by_bookmaker: dict[str, int]
    closing_ou: int
    issues: int = 0

    @property
    def with_score_pct(self) -> float:
        return _pct(self.with_score, self.finished)

    @property
    def time_known_pct(self) -> float:
        return _pct(self.time_known, self.fixtures)


@dataclass(slots=True)
class DataReport:
    generated_at: datetime
    rows: list[CoverageRow]
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    def exit_code(self, *, strict: bool = False) -> int:
        if self.errors or (strict and self.warnings):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "rows": [
                {
                    **asdict(r),
                    "with_score_pct": r.with_score_pct,
                    "time_known_pct": r.time_known_pct,
                }
                for r in self.rows
            ],
            "issues": [asdict(i) for i in self.issues],
            "summary": {"errors": len(self.errors), "warnings": len(self.warnings)},
        }


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _is_canonical_season(season: str) -> bool:
    match = _SEASON_SPLIT.match(season)
    if match:
        return int(match.group(2)) == int(match.group(1)) + 1
    return bool(_SEASON_CALENDAR.match(season))


async def build_report(
    session: AsyncSession,
    *,
    leagues: list[str] | None = None,
    seasons: list[str] | None = None,
    now: datetime | None = None,
) -> DataReport:
    now = now or datetime.now(UTC)
    scope = []
    if leagues:
        scope.append(League.code.in_(leagues))
    if seasons:
        scope.append(Fixture.season.in_(seasons))

    fixtures = (
        await session.execute(
            select(Fixture, League.code).join(League, League.id == Fixture.league_id).where(*scope)
        )
    ).all()
    by_id: dict[uuid.UUID, tuple[Fixture, str]] = {fx.id: (fx, code) for fx, code in fixtures}
    # The scope as a subquery, never as a list of ids: a full-history run has
    # tens of thousands of fixtures, more than asyncpg's 32,767 bind parameters.
    scoped_ids = (
        select(Fixture.id).join(League, League.id == Fixture.league_id).where(*scope)
    ).scalar_subquery()
    issues: list[Issue] = []

    def add(
        severity: str,
        code: str,
        fx: Fixture | None,
        league: str,
        season: str,
        detail: str,
        ids: list[uuid.UUID] | None = None,
    ) -> None:
        issues.append(
            Issue(
                severity=severity,
                code=code,
                league=league,
                season=season,
                detail=detail,
                fixture_ids=[str(i) for i in (ids or ([fx.id] if fx is not None else []))],
            )
        )

    # --- per-fixture checks -------------------------------------------------
    for fx, code in fixtures:
        if fx.status == FixtureStatus.finished:
            if fx.ft_home is None or fx.ft_away is None:
                add(
                    ERROR,
                    "finished_without_score",
                    fx,
                    code,
                    fx.season,
                    "finished, no full-time score",
                )
            if fx.kickoff_at > now:
                add(
                    ERROR,
                    "finished_in_future",
                    fx,
                    code,
                    fx.season,
                    f"finished but kicks off {fx.kickoff_at.isoformat()}",
                )
        scores = [s for s in (fx.ft_home, fx.ft_away, fx.ht_home, fx.ht_away) if s is not None]
        if any(s < 0 or s > MAX_GOALS for s in scores) or (
            fx.ht_home is not None
            and fx.ft_home is not None
            and fx.ht_away is not None
            and fx.ft_away is not None
            and (fx.ht_home > fx.ft_home or fx.ht_away > fx.ft_away)
        ):
            add(
                ERROR,
                "impossible_score",
                fx,
                code,
                fx.season,
                f"FT {fx.ft_home}-{fx.ft_away} HT {fx.ht_home}-{fx.ht_away}",
            )

    # --- duplicates across sources (any season label) -----------------------
    # The partner may sit outside the scope (e.g. the same match filed under
    # season "2026" while scoping to "2026-2027"), so its fields come from the
    # query, not from the scoped fixture set.
    other = aliased(Fixture)
    dup_scope = [or_(Fixture.id.in_(scoped_ids), other.id.in_(scoped_ids))] if scope else []
    duplicate_pairs = (
        await session.execute(
            select(
                League.code,
                Fixture.id,
                Fixture.season,
                Fixture.kickoff_at,
                other.id,
                other.season,
                other.kickoff_at,
            )
            .join(League, League.id == Fixture.league_id)
            .join(
                other,
                and_(
                    other.league_id == Fixture.league_id,
                    other.home_team_id == Fixture.home_team_id,
                    other.away_team_id == Fixture.away_team_id,
                    other.id > Fixture.id,
                    func.abs(func.extract("epoch", other.kickoff_at - Fixture.kickoff_at))
                    <= DUPLICATE_WINDOW.total_seconds(),
                ),
            )
            .where(*dup_scope)
        )
    ).all()
    hours = int(DUPLICATE_WINDOW.total_seconds() // 3600)
    for code, a, season_a, kickoff_a, b, season_b, kickoff_b in duplicate_pairs:
        add(
            ERROR,
            "duplicate_fixture",
            None,
            code,
            season_a if a in by_id else season_b,
            f"same pairing within {hours}h: {season_a} {kickoff_a.isoformat()} "
            f"vs {season_b} {kickoff_b.isoformat()}",
            [a, b],
        )

    # --- odds ---------------------------------------------------------------
    odds_query = select(Odds).where(Odds.fixture_id.in_(scoped_ids)) if scope else select(Odds)
    odds_rows = (await session.execute(odds_query)).scalars().all() if by_id else []
    snapshots: dict[tuple[uuid.UUID, str, str, datetime], dict[str, float]] = defaultdict(dict)
    # Snapshots with an unusable price stay reported as errors but never count
    # as coverage.
    invalid: set[tuple[uuid.UUID, str, str, datetime]] = set()
    # Pre-closing snapshots never count as closing coverage.
    not_closing: set[tuple[uuid.UUID, str, str, datetime]] = set()
    for o in odds_rows:
        if o.fixture_id not in by_id:  # pragma: no cover - written after the fixture scan
            continue
        fx, code = by_id[o.fixture_id]
        price = float(o.price)
        if not MIN_PRICE < price <= MAX_PRICE:
            add(
                ERROR,
                "odds_price_out_of_range",
                fx,
                code,
                fx.season,
                f"{o.bookmaker} {o.market} {o.outcome} = {price}",
            )
            invalid.add((o.fixture_id, o.bookmaker, o.market, o.ts))
        if o.is_closing and o.ts > fx.kickoff_at:
            add(
                ERROR,
                "closing_after_kickoff",
                fx,
                code,
                fx.season,
                f"{o.bookmaker} {o.market} closing quote at {o.ts.isoformat()} after kickoff",
            )
        snapshots[(o.fixture_id, o.bookmaker, o.market, o.ts)][o.outcome] = price
        if not o.is_closing:
            not_closing.add((o.fixture_id, o.bookmaker, o.market, o.ts))

    closing_1x2: dict[uuid.UUID, set[str]] = defaultdict(set)
    closing_ou: set[uuid.UUID] = set()
    for (fixture_id, bookmaker, market, ts), prices in snapshots.items():
        fx, code = by_id[fixture_id]
        expected = MARKET_OUTCOMES.get(market)
        if expected is not None and len(prices) != expected:
            add(
                WARNING,
                "incomplete_odds_snapshot",
                fx,
                code,
                fx.season,
                f"{bookmaker} {market} at {ts.isoformat()}: {sorted(prices)}",
            )
            continue
        key = (fixture_id, bookmaker, market, ts)
        if ts > fx.kickoff_at or key in invalid or key in not_closing:
            continue
        if market == "1x2":
            closing_1x2[fixture_id].add(bookmaker)
            if all(p > 0 for p in prices.values()):
                overround = sum(1.0 / p for p in prices.values())
                low, high = OVERROUND_RANGE
                if not low <= overround <= high:
                    add(
                        WARNING,
                        "overround_out_of_range",
                        fx,
                        code,
                        fx.season,
                        f"{bookmaker} 1x2 overround {overround:.3f}",
                    )
        elif market == "ou_2.5":
            closing_ou.add(fixture_id)

    # --- disagreements between sources (recorded, never applied) ---------------
    conflict_rows = (
        (
            await session.execute(
                select(IngestionConflict).where(
                    IngestionConflict.fixture_id.in_(scoped_ids) if scope else true()
                )
            )
        )
        .scalars()
        .all()
    )
    for c in conflict_rows:
        scoped = by_id.get(c.fixture_id)
        if scoped is None:  # pragma: no cover - scoped by the query
            continue
        fx, code = scoped
        add(
            WARNING,
            "score_conflict_across_sources",
            fx,
            code,
            fx.season,
            f"{c.provider} reports {c.field} = {c.incoming}, stored {c.existing} "
            f"(from {fx.source}); not applied",
        )

    # --- per (league, season) coverage and season-level checks ----------------
    groups: dict[tuple[str, str], list[Fixture]] = defaultdict(list)
    for fx, code in fixtures:
        groups[(code, fx.season)].append(fx)

    rows: list[CoverageRow] = []
    for (code, season), group in sorted(groups.items()):
        teams = {fx.home_team_id for fx in group} | {fx.away_team_id for fx in group}
        n = len(teams)
        finished = [fx for fx in group if fx.status == FixtureStatus.finished]
        bookmakers: dict[str, int] = defaultdict(int)
        for fx in finished:
            for bm in closing_1x2.get(fx.id, ()):
                bookmakers[bm] += 1
        row = CoverageRow(
            league=code,
            season=season,
            fixtures=len(group),
            finished=len(finished),
            with_score=sum(
                1 for fx in finished if fx.ft_home is not None and fx.ft_away is not None
            ),
            teams=n,
            expected_fixtures=n * (n - 1),
            time_known=sum(1 for fx in group if fx.kickoff_time_known),
            closing_1x2_by_bookmaker=dict(sorted(bookmakers.items())),
            closing_ou=sum(1 for fx in finished if fx.id in closing_ou),
        )
        rows.append(row)

        if not _is_canonical_season(season):
            add(
                WARNING,
                "non_canonical_season",
                None,
                code,
                season,
                f"season label {season!r} is neither YYYY-YYYY nor YYYY",
                [fx.id for fx in group][:20],
            )
        if n not in TYPICAL_TEAM_COUNTS:
            add(WARNING, "unusual_team_count", None, code, season, f"{n} teams")
        season_over = max(fx.kickoff_at for fx in group) < now - SEASON_OVER_AFTER
        if season_over:
            if len(group) < row.expected_fixtures:
                add(
                    WARNING,
                    "season_incomplete",
                    None,
                    code,
                    season,
                    f"{len(group)} of {row.expected_fixtures} fixtures",
                )
            played: dict[uuid.UUID, int] = defaultdict(int)
            for fx in group:
                played[fx.home_team_id] += 1
                played[fx.away_team_id] += 1
            uneven = sorted(c for c in played.values() if c != 2 * (n - 1))
            if uneven:
                add(
                    WARNING,
                    "uneven_team_schedule",
                    None,
                    code,
                    season,
                    f"{len(uneven)} teams without {2 * (n - 1)} matches: {uneven[:10]}",
                )
        if finished:
            covered = sum(1 for fx in finished if closing_1x2.get(fx.id))
            if covered / len(finished) < MIN_ODDS_COVERAGE:
                add(
                    WARNING,
                    "odds_coverage_low",
                    None,
                    code,
                    season,
                    f"closing 1x2 for {covered} of {len(finished)} finished fixtures",
                )

    counts: dict[tuple[str, str], int] = defaultdict(int)
    for issue in issues:
        counts[(issue.league, issue.season)] += 1
    for row in rows:
        row.issues = counts[(row.league, row.season)]

    severity_rank = {ERROR: 0, WARNING: 1}
    issues.sort(key=lambda i: (severity_rank[i.severity], i.code, i.league, i.season))
    return DataReport(generated_at=now, rows=rows, issues=issues)


def format_report(report: DataReport, *, max_issues_per_code: int = 20) -> str:
    """Human-readable table + issue list (capped per code; JSON has all)."""
    bookmakers = sorted({bm for r in report.rows for bm in r.closing_1x2_by_bookmaker})
    header = [
        "league",
        "season",
        "fixtures",
        "expected",
        "teams",
        "score%",
        "time%",
        *[f"1x2:{bm}" for bm in bookmakers],
        "ou2.5",
        "issues",
    ]
    lines = [" | ".join(header)]
    for r in report.rows:
        lines.append(
            " | ".join(
                [
                    r.league,
                    r.season,
                    str(r.fixtures),
                    str(r.expected_fixtures),
                    str(r.teams),
                    f"{r.with_score_pct:.1f}",
                    f"{r.time_known_pct:.1f}",
                    *[str(r.closing_1x2_by_bookmaker.get(bm, 0)) for bm in bookmakers],
                    str(r.closing_ou),
                    str(r.issues),
                ]
            )
        )
    lines.append("")
    lines.append(f"errors: {len(report.errors)}  warnings: {len(report.warnings)}")
    shown: dict[str, int] = defaultdict(int)
    for issue in report.issues:
        shown[issue.code] += 1
        if shown[issue.code] <= max_issues_per_code:
            ids = f" [{', '.join(issue.fixture_ids[:3])}]" if issue.fixture_ids else ""
            where = f"{issue.code} {issue.league} {issue.season}"
            lines.append(f"{issue.severity.upper():7} {where}: {issue.detail}{ids}")
    for code, total in sorted(shown.items()):
        if total > max_issues_per_code:
            lines.append(f"... {total - max_issues_per_code} more {code} (see --json)")
    return "\n".join(lines)


__all__ = [
    "ERROR",
    "WARNING",
    "CoverageRow",
    "DataReport",
    "Issue",
    "build_report",
    "format_report",
]
