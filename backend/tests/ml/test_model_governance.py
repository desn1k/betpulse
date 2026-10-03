"""Model governance: one global champion enforced by the database, the registry
lock (re-evaluation skips, admin actions wait with a timeout), the honest
in-play baseline label, model-version traceability of served probabilities, and
the leakage-free running league xG prior. Real Postgres."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.core.security import create_access_token
from app.ml import registry
from app.ml.features import DEFAULT_LEAGUE_XG, build_feature_table
from app.ml.registry import MethodMetrics, apply_champion_selection, rollback_to_snapshot
from app.ml.registry_lock import RegistryBusy, lock_registry, try_lock_registry
from app.ml.xg import XgModel
from app.models.audit_log import AuditLog
from app.models.fixture import Fixture, FixtureStats, FixtureStatus
from app.models.live import LiveUpdate
from app.models.model_registry import ModelRegistry, ModelRegistrySnapshot, ModelStatus
from app.models.prediction import Prediction, PredictionLive
from app.models.reference import League, Team
from app.models.user import User, UserRole, UserTier
from app.services import model_admin
from app.services.live.recompute import LIVE_BASELINE_NOTE, BaseRates, recompute_fixture
from app.workers.tasks import _swing_text, reevaluate_champions
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.workers.test_champion_reeval import _NOW, _seed

T0 = datetime(2026, 7, 1, tzinfo=UTC)
LOW = uuid.UUID(int=1)
HIGH = uuid.UUID(int=2**128 - 1)


async def _rows(
    methods: dict[str, uuid.UUID | None], champion: str | None = None
) -> dict[str, uuid.UUID]:
    """Committed registry rows (explicit ids where given) with one optional champion."""
    async with _write_sessionmaker()() as s:
        rows = {
            m: ModelRegistry(
                id=rid or uuid.uuid4(),
                method=m,
                version="v1",
                status=ModelStatus.champion if m == champion else ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("0"),
                min_samples=1,
                sample_count=500,
                accuracy_pct=Decimal("10"),
            )
            for m, rid in methods.items()
        }
        s.add_all(rows.values())
        await s.commit()
        return {m: r.id for m, r in rows.items()}


async def _champions() -> list[str]:
    async with _write_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(ModelRegistry.method).where(ModelRegistry.status == ModelStatus.champion)
                )
            ).scalars()
        )


# --- 1. one global champion -------------------------------------------------


@pytest.mark.asyncio
async def test_database_rejects_a_second_champion() -> None:
    ids = await _rows({"elo": None, "dixon_coles": None})
    async with _write_sessionmaker()() as s:
        for rid in ids.values():
            (await s.get(ModelRegistry, rid)).status = ModelStatus.champion  # type: ignore[union-attr]
        with pytest.raises(IntegrityError):
            await s.commit()


@pytest.mark.asyncio
async def test_promote_demotes_the_old_champion_before_promoting() -> None:
    """The new champion's key sorts first, so one flush covering both changes
    would UPDATE it first and break the index. The demotion must reach the
    database before — by an explicit flush, not an incidental one."""
    ids = await _rows({"elo": LOW, "dixon_coles": HIGH}, champion="dixon_coles")
    async with _write_sessionmaker()() as s:
        await model_admin.promote(s, ids["elo"], actor="admin:x")
        await s.commit()
    assert await _champions() == ["elo"]


@pytest.mark.asyncio
async def test_reevaluation_demotes_before_promoting(session: AsyncSession) -> None:
    await _rows({"elo": LOW, "dixon_coles": HIGH}, champion="dixon_coles")
    metrics = {
        "elo": MethodMetrics(
            accuracy_pct=60, brier=0.50, log_loss=0.9, roi_vs_closing=0, sample_count=500
        ),
        "dixon_coles": MethodMetrics(
            accuracy_pct=50, brier=0.60, log_loss=1.0, roi_vs_closing=0, sample_count=500
        ),
    }
    assert await apply_champion_selection(session, metrics, min_samples=1) == "elo"
    await session.commit()
    assert await _champions() == ["elo"]


@pytest.mark.asyncio
async def test_rollback_restores_the_champion_without_two_at_once(session: AsyncSession) -> None:
    ids = await _rows({"elo": LOW, "dixon_coles": HIGH}, champion="elo")
    snapshot = await registry.snapshot_registry(session, reason="test")
    await session.commit()
    await model_admin.promote(session, ids["dixon_coles"], actor="admin:x")
    await session.commit()
    # A version trained after the snapshot holds the title: it is demoted too.
    newer = ModelRegistry(
        method="xg", version="v2", status=ModelStatus.challenger, min_samples=1, sample_count=1
    )
    session.add(newer)
    await session.commit()
    await model_admin.promote(session, newer.id, actor="admin:x")
    await session.commit()

    await rollback_to_snapshot(session, snapshot.id)
    await session.commit()
    assert await _champions() == ["elo"]


@pytest.mark.asyncio
async def test_rollback_of_a_snapshot_with_two_champions_is_refused(session: AsyncSession) -> None:
    await _rows({"elo": None, "dixon_coles": None}, champion="elo")
    item = {"version": "v1", "status": "champion", "is_enabled": True, "is_visible": True}
    snap = ModelRegistrySnapshot(
        reason="legacy",
        payload=[
            {**item, "method": m, "display_weight": 0, "accuracy_pct": None}
            for m in ("elo", "dixon_coles")
        ],
    )
    session.add(snap)
    await session.commit()
    with pytest.raises(registry.SnapshotHasManyChampions):
        await rollback_to_snapshot(session, snap.id)


@pytest.mark.asyncio
async def test_concurrent_manual_promotions_leave_one_champion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _rows({"elo": None, "dixon_coles": None})
    original = model_admin.record_event

    async def slow_audit(*args: object, **kwargs: object) -> object:
        # Decided, not committed yet: the other promotion must wait for us.
        await asyncio.sleep(0.3)
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(model_admin, "record_event", slow_audit)

    async def promote(row_id: uuid.UUID) -> None:
        async with _write_sessionmaker()() as s:
            await model_admin.promote(s, row_id, actor="admin:x")
            await s.commit()

    await asyncio.wait_for(
        asyncio.gather(promote(ids["elo"]), promote(ids["dixon_coles"])), timeout=30
    )
    assert len(await _champions()) == 1


# --- 2. registry lock -------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_reevaluations_promote_once(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(session)
    await session.commit()
    original = registry.snapshot_registry

    async def slow_snapshot(*args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.3)
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(registry, "snapshot_registry", slow_snapshot)

    async def run() -> str | None:
        async with _write_sessionmaker()() as s:
            champion = await reevaluate_champions(
                s, window_days=90, min_samples=1, weight_mode="auto", now=_NOW
            )
            await s.commit()
            return champion

    results = await asyncio.wait_for(asyncio.gather(run(), run()), timeout=30)
    assert sorted(results, key=str) == sorted(["dixon_coles", None], key=str)  # one skipped
    async with _write_sessionmaker()() as s:
        promotions = await s.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "model.champion.promoted")
        )
        snapshots = await s.scalar(select(func.count()).select_from(ModelRegistrySnapshot))
    assert (promotions, snapshots) == (1, 1)


@pytest.mark.asyncio
async def test_reevaluation_skips_while_an_admin_action_holds_the_lock(
    session: AsyncSession,
) -> None:
    await _seed(session)
    await session.commit()
    await lock_registry(session)  # held until this session's transaction ends
    async with _write_sessionmaker()() as s:
        assert (
            await reevaluate_champions(
                s, window_days=90, min_samples=1, weight_mode="auto", now=_NOW
            )
            is None
        )
        await s.commit()
    assert await _champions() == []
    await session.rollback()
    async with _write_sessionmaker()() as s:
        assert (
            await reevaluate_champions(
                s, window_days=90, min_samples=1, weight_mode="auto", now=_NOW
            )
            == "dixon_coles"
        )


@pytest.mark.asyncio
async def test_admin_action_waits_then_fails_with_registry_busy(session: AsyncSession) -> None:
    ids = await _rows({"elo": None})
    assert await try_lock_registry(session)  # e.g. a running re-evaluation
    async with _write_sessionmaker()() as s:
        started = time.monotonic()
        with pytest.raises(RegistryBusy):
            await lock_registry(s, timeout_ms=200)
        assert 0.15 < time.monotonic() - started < 5
        await s.rollback()
        # The timeout applied to that wait only; once free, the action goes through.
        await session.rollback()
        await model_admin.promote(s, ids["elo"], actor="admin:x")
        await s.commit()
        assert await s.scalar(select(func.current_setting("lock_timeout"))) == "0"
    assert await _champions() == ["elo"]


async def _admin_headers(session: AsyncSession) -> dict[str, str]:
    admin = User(
        email=f"{uuid.uuid4()}@x.com",
        password_hash="x",
        role=UserRole.admin,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(admin)
    await session.commit()
    return {"Authorization": f"Bearer {create_access_token(subject=str(admin.id), role='admin')}"}


@pytest.mark.asyncio
async def test_admin_endpoint_answers_409_registry_busy_instead_of_hanging(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _rows({"elo": None})
    headers = await _admin_headers(session)
    monkeypatch.setattr(get_settings(), "registry_lock_timeout_ms", 200)
    assert await try_lock_registry(session)

    resp = await asyncio.wait_for(
        client.post(f"/admin/models/{ids['elo']}/promote", headers=headers), timeout=10
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "registry_busy"
    await session.rollback()
    assert await _champions() == []

    resp = await client.post(f"/admin/models/{ids['elo']}/promote", headers=headers)
    assert resp.status_code == 200
    assert await _champions() == ["elo"]


# --- 3. the in-play baseline is labelled as such ----------------------------


async def _live_fixture(session: AsyncSession) -> uuid.UUID:
    league = League(code="EPL", name="Premier League", country="England")
    home = Team(name="Arsenal", normalized_name="arsenal")
    away = Team(name="Chelsea", normalized_name="chelsea")
    session.add_all([league, home, away])
    await session.flush()
    fx = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(UTC),
        status=FixtureStatus.live,
        minute=1,
    )
    session.add(fx)
    await session.flush()
    return fx.id


@pytest.mark.asyncio
async def test_live_numbers_are_stored_and_served_as_the_baseline(
    client: AsyncClient, session: AsyncSession
) -> None:
    fid = await _live_fixture(session)
    await recompute_fixture(
        session,
        fixture_id=fid,
        minute=30,
        home_score=1,
        away_score=0,
        base_rates=BaseRates(lam_home=1.45, lam_away=1.15),
        swing_threshold=0.1,
    )
    await session.commit()

    methods = set((await session.execute(select(PredictionLive.method))).scalars())
    assert methods == {"live_baseline"}
    update = (await session.execute(select(LiveUpdate))).scalar_one()
    assert update.payload["method"] == "live_baseline"
    assert update.payload["model_version"] == "live-baseline-v1"
    assert update.payload["team_strength"] is False

    resp = await client.get(f"/live/push/latest/{fid}")
    assert resp.status_code == 200  # the flat stored probs no longer break the response
    body = resp.json()
    assert body["probs"]["1x2"]["home"] == pytest.approx(update.payload["probs"]["home"])
    assert (body["method"], body["model_version"], body["team_strength"]) == (
        "live_baseline",
        "live-baseline-v1",
        False,
    )
    assert body["note"] == LIVE_BASELINE_NOTE


def test_swing_push_text_names_its_basis_and_claims_no_strength() -> None:
    text = _swing_text(uuid.uuid4(), 63, {"home": 0.5, "draw": 0.3, "away": 0.2})
    assert LIVE_BASELINE_NOTE in text
    assert "63'" in text and "П1 50%" in text
    lowered = text.lower()
    for claim in ("сильн", "преимуществ", "edge", "value", "форма"):
        assert claim not in lowered


# --- 4. served probabilities name their model -------------------------------


@pytest.mark.asyncio
async def test_served_probabilities_carry_model_version_and_mlflow_run(
    client: AsyncClient, session: AsyncSession
) -> None:
    league = League(code="EPL", name="Premier League")
    home, away = Team(name="A", normalized_name="a"), Team(name="B", normalized_name="b")
    session.add_all([league, home, away])
    await session.flush()
    fx = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(UTC) + timedelta(hours=6),
        status=FixtureStatus.scheduled,
    )
    session.add(fx)
    await session.flush()
    for method, version, run in (("elo", "v2", "run-elo-2"), ("consensus", "v2", "run-cons-2")):
        session.add(
            ModelRegistry(
                method=method,
                version=version,
                mlflow_run_id=run,
                status=ModelStatus.challenger,
                is_visible=True,
                display_weight=Decimal("50"),
                sample_count=1,
            )
        )
        for outcome, p in (("home", "0.5"), ("draw", "0.3"), ("away", "0.2")):
            session.add(
                Prediction(
                    fixture_id=fx.id,
                    method=method,
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal(p),
                    model_version=version,
                )
            )
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.pro)
    session.add(user)
    await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}

    detail = (await client.get(f"/matches/{fx.id}", headers=headers)).json()
    (elo,) = detail["methods"]
    assert (elo["model_version"], elo["mlflow_run_id"]) == ("v2", "run-elo-2")
    assert (detail["consensus_model_version"], detail["consensus_mlflow_run_id"]) == (
        "v2",
        "run-cons-2",
    )
    (item,) = (await client.get("/matches", headers=headers)).json()["items"]
    assert (item["consensus_model_version"], item["consensus_mlflow_run_id"]) == (
        "v2",
        "run-cons-2",
    )


# --- 5. running league xG prior ---------------------------------------------


def _approx_xg(shots: int, on_target: int) -> float:
    return XgModel(has_coordinates=False).approximate_match_xg(shots, on_target)


@pytest.mark.asyncio
async def test_league_xg_prior_is_the_running_average_before_the_batch(
    session: AsyncSession,
) -> None:
    epl = League(code="EPL", name="EPL", season_start_month=8)
    liga = League(code="LALIGA", name="LaLiga", season_start_month=8)
    teams = [Team(name=f"t{i}", normalized_name=f"t{i}", country="E") for i in range(10)]
    session.add_all([epl, liga, *teams])
    await session.flush()

    def match(league: League, h: int, a: int, day: int) -> Fixture:
        return Fixture(
            league_id=league.id,
            season="2025-2026",
            home_team_id=teams[h].id,
            away_team_id=teams[a].id,
            kickoff_at=T0 + timedelta(days=day),
            status=FixtureStatus.finished,
            ft_home=1,
            ft_away=1,
        )

    first = match(epl, 0, 1, 0)
    # Same instant as ``first``: must not see first's shots (same batch).
    same_batch = match(epl, 2, 3, 0)
    newcomer = match(epl, 4, 5, 7)
    other_league = match(liga, 6, 7, 7)
    later = match(epl, 8, 9, 14)
    session.add_all([first, same_batch, newcomer, other_league, later])
    await session.flush()
    session.add_all(
        [
            FixtureStats(
                fixture_id=first.id,
                home_shots=30,
                away_shots=10,
                home_shots_on_target=15,
                away_shots_on_target=3,
            ),
            # Shots of a later match must never feed an earlier prior.
            FixtureStats(
                fixture_id=newcomer.id,
                home_shots=2,
                away_shots=2,
                home_shots_on_target=0,
                away_shots_on_target=0,
            ),
        ]
    )
    await session.flush()

    df = (await build_feature_table(session)).set_index("fixture_id")
    after_first = (_approx_xg(30, 15) + _approx_xg(10, 3)) / 2
    assert after_first != pytest.approx(DEFAULT_LEAGUE_XG)

    assert df.loc[first.id, "rolling_xg_home"] == pytest.approx(DEFAULT_LEAGUE_XG)
    assert df.loc[same_batch.id, "rolling_xg_home"] == pytest.approx(DEFAULT_LEAGUE_XG)
    assert df.loc[newcomer.id, "rolling_xg_home"] == pytest.approx(after_first)
    assert df.loc[newcomer.id, "rolling_xg_away"] == pytest.approx(after_first)
    # Another league keeps its own prior (no data yet → the default).
    assert df.loc[other_league.id, "rolling_xg_home"] == pytest.approx(DEFAULT_LEAGUE_XG)
    after_newcomer = (2 * after_first + 2 * _approx_xg(2, 0)) / 4
    assert df.loc[later.id, "rolling_xg_home"] == pytest.approx(after_newcomer)
