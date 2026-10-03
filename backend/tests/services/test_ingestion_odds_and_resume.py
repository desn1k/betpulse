"""HI-3: closing vs pre-closing quotes, date-based reference bookmaker, score
and kickoff corrections, cross-source conflicts, resumable per-pair runs."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.config import ReferenceBookmakerRange, Settings, get_settings
from app.core.db import _write_sessionmaker
from app.ml.evaluation import compute_rolling_metrics
from app.ml.odds_selection import closing_quotes, reference_bookmaker_for
from app.models.audit_log import AuditLog
from app.models.fixture import Fixture
from app.models.ingestion_run import IngestionConflict, IngestionRun, IngestionStatus
from app.models.market import Odds
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, ProviderLeagueAlias, ProviderTeamAlias, Team
from app.providers.dtos import (
    MAX_PRICE,
    MIN_PRICE,
    BookmakerOddsDTO,
    FixtureDTO,
    LeagueRef,
    TeamRef,
)
from app.providers.football_data_couk import PRE_CLOSING_LEAD, FootballDataCoUkProvider
from app.services.data_quality import build_report
from app.services.ingestion.core import IngestSummary, LeagueMeta, ingest_records
from app.services.ingestion.runner import mark_stale_runs, run_recorded_ingestion
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

EPL = LeagueMeta("Premier League", "England", 8)
KICKOFF = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)
FD_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "football_data"


def _quotes(
    bookmaker: str, prices: tuple[str, str, str], ts: datetime, *, closing: bool
) -> list[BookmakerOddsDTO]:
    return [
        BookmakerOddsDTO(
            bookmaker=bookmaker,
            market="1x2",
            outcome=o,
            price=Decimal(p),
            ts=ts,
            is_closing=closing,
        )
        for o, p in zip(("home", "draw", "away"), prices, strict=True)
    ]


def _dto(
    provider: str,
    external_id: str,
    *,
    kickoff: datetime = KICKOFF,
    score: tuple[int, int] | None = (2, 1),
    odds: list[BookmakerOddsDTO] | None = None,
    home: str = "Arsenal",
    away: str = "Chelsea",
) -> FixtureDTO:
    return FixtureDTO(
        provider=provider,
        external_id=external_id,
        league=LeagueRef(raw_name="EPL", raw_code="EPL"),
        season="2023-2024",
        home=TeamRef(raw_name=home),
        away=TeamRef(raw_name=away),
        kickoff_at=kickoff,
        status="finished" if score else "scheduled",
        ft_home=score[0] if score else None,
        ft_away=score[1] if score else None,
        odds=odds or [],
    )


async def _ingest(
    session: AsyncSession, provider: str, dtos: list[FixtureDTO], *, seed: bool = True
) -> IngestSummary:
    return await ingest_records(
        session, dtos, provider=provider, league_code="EPL", seed=seed, meta=EPL
    )


# --- (a) pre-closing is never closing ------------------------------------------


_CSV = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSD,PSA,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA,"
    "P>2.5,P<2.5,PC>2.5,PC<2.5,AvgC>2.5,AvgC<2.5\n"
    "E0,02/03/2024,15:00,Arsenal,Chelsea,2,1,1.90,3.50,4.20,2.00,3.40,4.00,2.05,3.35,3.90,"
    "1.80,2.05,1.85,2.00,1.88,1.97\n"
    "E0,09/03/2024,15:00,Burnley,Fulham,0,0,2.60,3.30,2.80,,,,,,,1.95,1.90,,,,\n"
)


def test_adapter_stores_closing_and_pre_closing_as_what_they_are() -> None:
    full, pre_only = FootballDataCoUkProvider().parse_csv(_CSV.encode(), "EPL", "2023-2024")
    kinds = {(q.bookmaker, q.market, q.is_closing, q.ts) for q in full.odds}
    assert kinds == {
        ("pinnacle", "1x2", True, full.kickoff_at),
        ("pinnacle", "1x2", False, full.kickoff_at - PRE_CLOSING_LEAD),
        ("market_avg", "1x2", True, full.kickoff_at),
        ("pinnacle", "ou_2.5", True, full.kickoff_at),
        ("pinnacle", "ou_2.5", False, full.kickoff_at - PRE_CLOSING_LEAD),
        ("market_avg", "ou_2.5", True, full.kickoff_at),
    }
    closing_home = {
        q.bookmaker: q.price
        for q in full.odds
        if q.market == "1x2" and q.outcome == "home" and q.is_closing
    }
    assert closing_home == {"pinnacle": Decimal("2.00"), "market_avg": Decimal("2.05")}
    # Only the pre-closing columns filled: nothing is labelled closing.
    assert pre_only.odds and not any(q.is_closing for q in pre_only.odds)


@pytest.mark.asyncio
async def test_core_rejects_quotes_breaking_the_time_rule(session: AsyncSession) -> None:
    odds = (
        _quotes("pinnacle", ("2.0", "3.4", "4.0"), KICKOFF, closing=True)
        + _quotes("pinnacle", ("1.5", "4.0", "6.0"), KICKOFF + timedelta(minutes=20), closing=True)
        + _quotes("pinnacle", ("1.9", "3.5", "4.2"), KICKOFF, closing=False)
    )
    summary = await _ingest(session, "src_a", [_dto("src_a", "a-1", odds=odds)])
    assert summary.odds_rejected == 6
    rows = (await session.execute(select(Odds))).scalars().all()
    assert {(r.is_closing, r.ts) for r in rows} == {(True, KICKOFF)}


def test_adapter_skips_a_quote_set_with_an_out_of_range_price() -> None:
    row = _CSV.splitlines()[:2]
    row[1] = row[1].replace("2.05,3.35,3.90", "2.05,3.35,1500")  # AvgCA above 1000
    csv = "".join(f"{line}\n" for line in row)
    (fx,) = FootballDataCoUkProvider().parse_csv(csv.encode(), "EPL", "2023-2024")
    kinds = {(q.bookmaker, q.market) for q in fx.odds}
    assert ("market_avg", "1x2") not in kinds
    assert ("pinnacle", "1x2") in kinds and ("market_avg", "ou_2.5") in kinds


@pytest.mark.asyncio
async def test_core_rejects_out_of_range_prices(session: AsyncSession) -> None:
    odds = _quotes("pinnacle", ("2.0", "3.4", "4.0"), KICKOFF, closing=True) + _quotes(
        "market_avg", ("1.0", "3.4", "1001"), KICKOFF, closing=True
    )
    summary = await _ingest(session, "src_a", [_dto("src_a", "a-1", odds=odds)])
    assert summary.odds_rejected == 2
    rows = (await session.execute(select(Odds))).scalars().all()
    assert len(rows) == 4 and all(MIN_PRICE < float(r.price) <= MAX_PRICE for r in rows)


@pytest.mark.asyncio
async def test_time_rule_holds_for_the_stored_kickoff_of_a_linked_fixture(
    session: AsyncSession,
) -> None:
    """src_b lists the match 2h later than the stored fixture. Its quotes must
    obey the rule for the stored kickoff too, or a closing quote would sit after
    the real kickoff."""
    await _ingest(session, "src_a", [_dto("src_a", "a-1")])
    await _alias_b(session)
    later = KICKOFF + timedelta(hours=2)
    odds = (
        _quotes("b_closing_at_its_kickoff", ("2.0", "3.4", "4.0"), later, closing=True)
        + _quotes(
            "b_closing_in_play", ("1.5", "4.0", "6.0"), KICKOFF + timedelta(hours=1), closing=True
        )
        + _quotes(
            "b_pre_in_play", ("1.9", "3.5", "4.2"), KICKOFF + timedelta(minutes=30), closing=False
        )
        + _quotes("b_pre_ok", ("1.9", "3.5", "4.2"), KICKOFF - timedelta(hours=1), closing=False)
    )
    summary = await _ingest(
        session, "src_b", [_dto("src_b", "b-9", kickoff=later, odds=odds)], seed=False
    )
    assert summary.odds_rejected == 6
    rows = (await session.execute(select(Odds))).scalars().all()
    assert {(r.bookmaker, r.is_closing, r.ts) for r in rows} == {
        ("b_closing_at_its_kickoff", True, KICKOFF),
        ("b_pre_ok", False, KICKOFF - timedelta(hours=1)),
    }


@pytest.mark.asyncio
async def test_closing_quotes_ignore_pre_closing(session: AsyncSession) -> None:
    odds_full = _quotes("pinnacle", ("2.0", "3.4", "4.0"), KICKOFF, closing=True) + _quotes(
        "pinnacle", ("1.9", "3.5", "4.2"), KICKOFF - timedelta(days=1), closing=False
    )
    pre_only = _quotes(
        "pinnacle", ("2.5", "3.3", "2.9"), KICKOFF - timedelta(days=1), closing=False
    )
    later = KICKOFF + timedelta(days=7)
    await _ingest(
        session,
        "src_a",
        [
            _dto("src_a", "a-1", odds=odds_full),
            _dto(
                "src_a",
                "a-2",
                kickoff=later,
                home="Burnley",
                away="Fulham",
                odds=[q.model_copy(update={"ts": later - timedelta(days=1)}) for q in pre_only],
            ),
        ],
    )
    fixtures = (await session.execute(select(Fixture).order_by(Fixture.kickoff_at))).scalars().all()
    quotes = await closing_quotes(session, fixtures)
    assert quotes == {
        fixtures[0].id: {
            "home": Decimal("2.000"),
            "draw": Decimal("3.400"),
            "away": Decimal("4.000"),
        }
    }


@pytest.mark.asyncio
async def test_roi_vs_closing_excludes_matches_with_only_pre_closing_quotes(
    session: AsyncSession,
) -> None:
    first, second = KICKOFF, KICKOFF + timedelta(days=7)
    await _ingest(
        session,
        "src_a",
        [
            _dto(
                "src_a",
                "a-1",
                kickoff=first,
                odds=_quotes("pinnacle", ("2.0", "3.4", "4.0"), first, closing=True),
            ),
            _dto(
                "src_a",
                "a-2",
                kickoff=second,
                home="Burnley",
                away="Fulham",
                odds=_quotes(
                    "pinnacle", ("3.0", "3.4", "2.4"), second - timedelta(days=1), closing=False
                ),
            ),
        ],
    )
    session.add(
        ModelRegistry(method="elo", version="v1", status=ModelStatus.challenger, sample_count=2)
    )
    for fx in (await session.execute(select(Fixture))).scalars():
        for outcome, p in (("home", "0.9"), ("draw", "0.05"), ("away", "0.05")):
            session.add(
                Prediction(
                    fixture_id=fx.id,
                    method="elo",
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal(p),
                    model_version="v1",
                )
            )
    await session.flush()

    metrics = await compute_rolling_metrics(session, window_days=60, now=second + timedelta(days=1))

    # Both home wins at p=0.9. Only the first has a closing price (2.00 → +1.0);
    # the second's pre-closing 3.00 must not be bet (it would make ROI 1.5).
    assert metrics["elo"].roi_vs_closing == pytest.approx(1.0)
    assert metrics["elo"].sample_count == 2  # still scored for Brier etc.


# --- (b) reference bookmaker per date range ---------------------------------------


def test_reference_bookmaker_switches_on_2025_07_23() -> None:
    assert reference_bookmaker_for(datetime(2025, 7, 22, 23, 59, tzinfo=UTC)) == "pinnacle"
    assert reference_bookmaker_for(datetime(2025, 7, 23, 0, 0, tzinfo=UTC)) == "market_avg"
    assert reference_bookmaker_for(datetime(2019, 8, 9, 19, 0, tzinfo=UTC)) == "pinnacle"


@pytest.mark.asyncio
async def test_closing_quotes_use_each_fixtures_reference_bookmaker(session: AsyncSession) -> None:
    before = datetime(2025, 5, 10, 14, 0, tzinfo=UTC)
    after = datetime(2025, 8, 16, 14, 0, tzinfo=UTC)

    def both(ts: datetime) -> list[BookmakerOddsDTO]:
        return _quotes("pinnacle", ("2.0", "3.4", "4.0"), ts, closing=True) + _quotes(
            "market_avg", ("2.1", "3.3", "3.9"), ts, closing=True
        )

    await _ingest(
        session,
        "src_a",
        [
            _dto("src_a", "a-1", kickoff=before, odds=both(before)),
            _dto("src_a", "a-2", kickoff=after, home="Burnley", away="Fulham", odds=both(after)),
        ],
    )
    old, new = (await session.execute(select(Fixture).order_by(Fixture.kickoff_at))).scalars().all()
    quotes = await closing_quotes(session, [old, new])
    assert quotes[old.id]["home"] == Decimal("2.000")  # pinnacle
    assert quotes[new.id]["home"] == Decimal("2.100")  # market_avg


@pytest.mark.parametrize(
    "ranges",
    [
        [],
        [{"bookmaker": "a", "from_date": "2020-01-01"}],  # first range must be open
        [{"bookmaker": "a", "until_date": "2025-01-01"}],  # last range must be open
        [  # gap
            {"bookmaker": "a", "until_date": "2025-01-01"},
            {"bookmaker": "b", "from_date": "2025-02-01"},
        ],
        [  # overlap
            {"bookmaker": "a", "until_date": "2025-02-01"},
            {"bookmaker": "b", "from_date": "2025-01-01"},
        ],
    ],
)
def test_invalid_reference_bookmaker_ranges_are_rejected(ranges: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError, match="REFERENCE_BOOKMAKERS"):
        Settings(reference_bookmakers=ranges)


def test_reference_bookmakers_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        get_settings(),
        "reference_bookmakers",
        [
            ReferenceBookmakerRange(bookmaker="b365", until_date=date(2020, 1, 1)),
            ReferenceBookmakerRange(bookmaker="market_avg", from_date=date(2020, 1, 1)),
        ],
    )
    assert reference_bookmaker_for(datetime(2019, 6, 1, tzinfo=UTC)) == "b365"
    assert reference_bookmaker_for(datetime(2024, 6, 1, tzinfo=UTC)) == "market_avg"


# --- (c) corrections and conflicts ------------------------------------------------


async def _alias_b(session: AsyncSession) -> None:
    league = (await session.execute(select(League))).scalar_one()
    teams = {t.normalized_name: t for t in (await session.execute(select(Team))).scalars()}
    session.add_all(
        [
            ProviderLeagueAlias(provider="src_b", alias="EPL", league_id=league.id),
            ProviderTeamAlias(provider="src_b", alias="Arsenal", team_id=teams["arsenal"].id),
            ProviderTeamAlias(provider="src_b", alias="Chelsea", team_id=teams["chelsea"].id),
        ]
    )
    await session.flush()


@pytest.mark.asyncio
async def test_own_source_correction_is_applied_and_audited(session: AsyncSession) -> None:
    await _ingest(session, "src_a", [_dto("src_a", "a-1", score=(2, 1))])
    summary = await _ingest(session, "src_a", [_dto("src_a", "a-1", score=(2, 2))])

    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert (fixture.ft_home, fixture.ft_away) == (2, 2)
    assert summary.fixtures_corrected == 1
    audit = (
        await session.execute(
            select(AuditLog).where(AuditLog.action == "ingestion.fixture.corrected")
        )
    ).scalar_one()
    assert audit.meta["changes"] == {"ft_away": ["1", "2"]}
    assert audit.meta["provider"] == "src_a"


@pytest.mark.asyncio
async def test_other_source_disagreement_is_recorded_not_applied(session: AsyncSession) -> None:
    await _ingest(session, "src_a", [_dto("src_a", "a-1", score=(2, 1))])
    await _alias_b(session)
    summary = await _ingest(session, "src_b", [_dto("src_b", "b-9", score=(3, 1))], seed=False)

    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert (fixture.ft_home, fixture.ft_away) == (2, 1)  # untouched
    assert summary.conflicts == 1 and summary.fixtures_corrected == 0
    conflict = (await session.execute(select(IngestionConflict))).scalar_one()
    assert (conflict.provider, conflict.field, conflict.existing, conflict.incoming) == (
        "src_b",
        "ft_home",
        "2",
        "3",
    )
    report = await build_report(session, now=KICKOFF + timedelta(days=200))
    conflicts = [i for i in report.warnings if i.code == "score_conflict_across_sources"]
    assert len(conflicts) == 1 and "src_b" in conflicts[0].detail


@pytest.mark.asyncio
async def test_missing_result_is_filled_from_another_source(session: AsyncSession) -> None:
    await _ingest(session, "src_a", [_dto("src_a", "a-1", score=None)])
    await _alias_b(session)
    await _ingest(session, "src_b", [_dto("src_b", "b-9", score=(1, 0))], seed=False)
    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert (fixture.ft_home, fixture.ft_away, fixture.status.value) == (1, 0, "finished")
    assert await session.scalar(select(func.count()).select_from(IngestionConflict)) == 0


@pytest.mark.asyncio
async def test_postponed_match_moves_and_its_old_closing_quote_stops_being_closing(
    session: AsyncSession,
) -> None:
    await _ingest(
        session,
        "src_a",
        [
            _dto(
                "src_a",
                "a-1",
                score=None,
                odds=_quotes("pinnacle", ("2.0", "3.4", "4.0"), KICKOFF, closing=True)
                + _quotes(
                    "other", ("2.1", "3.3", "3.9"), KICKOFF - timedelta(hours=1), closing=True
                ),
            )
        ],
    )
    seeded = {
        (r.bookmaker, r.ts, r.is_closing) for r in (await session.execute(select(Odds))).scalars()
    }
    assert seeded == {("pinnacle", KICKOFF, True), ("other", KICKOFF - timedelta(hours=1), True)}
    moved = KICKOFF + timedelta(days=10)
    await _ingest(session, "src_a", [_dto("src_a", "a-1", kickoff=moved, score=None)])

    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert fixture.kickoff_at == moved
    rows = (await session.execute(select(Odds))).scalars().all()
    assert len(rows) == 6 and not any(r.is_closing for r in rows)
    assert await closing_quotes(session, [fixture]) == {}


@pytest.mark.asyncio
async def test_kickoff_moved_earlier_removes_quotes_at_or_after_the_new_kickoff(
    session: AsyncSession,
) -> None:
    moved = KICKOFF - timedelta(hours=3)
    odds = (
        _quotes("pinnacle", ("2.0", "3.4", "4.0"), KICKOFF, closing=True)
        + _quotes("other", ("2.1", "3.3", "3.9"), KICKOFF - timedelta(hours=2), closing=True)
        + _quotes("pre_in_play", ("1.9", "3.5", "4.2"), moved, closing=False)
        + _quotes("pre_ok", ("1.9", "3.5", "4.2"), KICKOFF - timedelta(days=1), closing=False)
    )
    await _ingest(session, "src_a", [_dto("src_a", "a-1", score=None, odds=odds)])
    await _ingest(session, "src_a", [_dto("src_a", "a-1", kickoff=moved, score=None)])

    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert fixture.kickoff_at == moved
    rows = (await session.execute(select(Odds))).scalars().all()
    assert {(r.bookmaker, r.is_closing) for r in rows} == {("pre_ok", False)}
    audit = (
        await session.execute(
            select(AuditLog).where(AuditLog.action == "ingestion.fixture.corrected")
        )
    ).scalar_one()
    assert audit.meta["odds_removed"] == 9
    report = await build_report(session, now=KICKOFF + timedelta(days=200))
    assert not [i for i in report.errors if i.code == "closing_after_kickoff"]


# --- (d) per-pair commits, resumable runs, stale rows ---------------------------------


async def _runs() -> list[IngestionRun]:
    async with _write_sessionmaker()() as s:
        return list(
            (await s.execute(select(IngestionRun).order_by(IngestionRun.started_at))).scalars()
        )


@pytest.mark.asyncio
async def test_committed_pairs_survive_a_later_failure_and_unchanged_pairs_are_skipped(
    session: AsyncSession,
) -> None:
    payload = (FD_DIR / "E0_2324.csv").read_bytes()
    calls: list[str] = []

    async def source(league: str, season: str) -> bytes:
        calls.append(league)
        if league == "LALIGA":
            raise RuntimeError("network down")
        return payload

    first = await run_recorded_ingestion(
        session,
        leagues=["EPL", "LALIGA"],
        seasons=["2023-2024"],
        csv_source=source,
        provider_name="football_data_couk",
    )
    assert [r.status for r in first] == [IngestionStatus.success, IngestionStatus.failed]
    async with _write_sessionmaker()() as other:  # committed, visible elsewhere
        assert (await other.scalar(select(func.count()).select_from(Fixture)) or 0) > 0

    second = await run_recorded_ingestion(
        session,
        leagues=["EPL"],
        seasons=["2023-2024"],
        csv_source=source,
        provider_name="football_data_couk",
    )
    assert second[0].skipped_reason == "unchanged" and second[0].records == 0

    forced = await run_recorded_ingestion(
        session,
        leagues=["EPL"],
        seasons=["2023-2024"],
        csv_source=source,
        provider_name="football_data_couk",
        force=True,
    )
    assert forced[0].skipped_reason is None and (forced[0].records or 0) > 0
    assert forced[0].fixtures_ingested == 0  # idempotent: found by external ref
    assert len({r.content_sha256 for r in await _runs() if r.content_sha256}) == 1


@pytest.mark.asyncio
async def test_only_runs_past_the_threshold_are_marked_stale(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    old = IngestionRun(
        provider="football_data_couk",
        league="EPL",
        season="2023-2024",
        status=IngestionStatus.running,
        started_at=now - timedelta(hours=2),
    )
    young = IngestionRun(
        provider="football_data_couk",
        league="EPL",
        season="2024-2025",
        status=IngestionStatus.running,
        started_at=now - timedelta(minutes=5),
    )
    other_provider = IngestionRun(
        provider="paid",
        league="EPL",
        season="2023-2024",
        status=IngestionStatus.running,
        started_at=now - timedelta(hours=2),
    )
    session.add_all([old, young, other_provider])
    await session.flush()

    marked = await mark_stale_runs(session, provider_name="football_data_couk", now=now)

    assert marked == 1
    for row in (old, young, other_provider):
        await session.refresh(row)
    assert old.status == IngestionStatus.failed and "stale" in (old.error or "")
    assert young.status == IngestionStatus.running  # possibly alive: untouched
    assert other_provider.status == IngestionStatus.running
    _ = uuid
