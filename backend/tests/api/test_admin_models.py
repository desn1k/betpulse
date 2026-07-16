"""Admin ML model management: RBAC, audit, weighting mode, manual weights,
promote/demote (+override), snapshot rollback + diff, retrain (Phase 12b)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from app.core.arq import get_arq_pool
from app.core.security import create_access_token
from app.main import app
from app.models.audit_log import AuditLog
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.user import User, UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class _FakePool:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    async def enqueue_job(self, name: str, *args: object, **_kwargs: object) -> None:
        self.jobs.append(name)


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


async def _user_headers(session: AsyncSession) -> dict[str, str]:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x")
    session.add(user)
    await session.commit()
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id), role='user')}"}


async def _seed_model(
    session: AsyncSession,
    *,
    method: str,
    accuracy: float | None = 60.0,
    sample_count: int = 500,
    status: ModelStatus = ModelStatus.challenger,
    min_samples: int = 300,
) -> uuid.UUID:
    row = ModelRegistry(
        method=method,
        version="v1",
        status=status,
        is_enabled=True,
        is_visible=True,
        display_weight=Decimal("0"),
        accuracy_pct=None if accuracy is None else Decimal(str(accuracy)),
        sample_count=sample_count,
        min_samples=min_samples,
    )
    session.add(row)
    await session.flush()
    return row.id


# --- listing + RBAC ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_defaults_to_auto(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_model(session, method="elo")
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.get("/admin/models", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["weighting_mode"] == "auto"
    assert body["models"][0]["method"] == "elo"


@pytest.mark.asyncio
async def test_model_endpoints_require_admin(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _user_headers(session)
    assert (await client.get("/admin/models", headers=headers)).status_code == 403
    assert (
        await client.put("/admin/models/weighting", headers=headers, json={"mode": "manual"})
    ).status_code == 403
    assert (
        await client.post(f"/admin/models/{uuid.uuid4()}/promote", headers=headers)
    ).status_code == 403
    assert (await client.post("/admin/models/retrain", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_patch_toggles_visibility_and_audits(
    client: AsyncClient, session: AsyncSession
) -> None:
    mid = await _seed_model(session, method="elo")
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.patch(
        f"/admin/models/{mid}", headers=headers, json={"is_visible": False, "notes": "hidden"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_visible"] is False

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "model.update")))
        .scalars()
        .all()
    )
    assert events and "is_visible" in events[0].meta["fields"]


# --- weighting mode + manual weights ---------------------------------------


@pytest.mark.asyncio
async def test_manual_weights_require_manual_mode_and_sum_100(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_model(session, method="elo", accuracy=60)
    await _seed_model(session, method="xg", accuracy=55)
    await session.commit()
    headers = await _admin_headers(session)

    # Auto mode → manual weights rejected.
    conflict = await client.put(
        "/admin/models/weights", headers=headers, json={"weights": {"elo": 50, "xg": 50}}
    )
    assert conflict.status_code == 409

    await client.put("/admin/models/weighting", headers=headers, json={"mode": "manual"})

    # Must sum to 100.
    bad = await client.put(
        "/admin/models/weights", headers=headers, json={"weights": {"elo": 40, "xg": 40}}
    )
    assert bad.status_code == 422

    ok = await client.put(
        "/admin/models/weights", headers=headers, json={"weights": {"elo": 70, "xg": 30}}
    )
    assert ok.status_code == 200
    weights = {m["method"]: m["display_weight"] for m in ok.json()["models"]}
    assert weights["elo"] == 70 and weights["xg"] == 30


@pytest.mark.asyncio
async def test_switch_to_auto_recomputes_weights_immediately(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_model(session, method="elo", accuracy=70)
    await _seed_model(session, method="xg", accuracy=50)
    await session.commit()
    headers = await _admin_headers(session)

    # Manual weights that contradict accuracy.
    await client.put("/admin/models/weighting", headers=headers, json={"mode": "manual"})
    await client.put(
        "/admin/models/weights", headers=headers, json={"weights": {"elo": 10, "xg": 90}}
    )

    # Flipping to auto recomputes softmax(accuracy) right away — no waiting for the
    # nightly re-eval. The more accurate method must now outweigh the other.
    resp = await client.put("/admin/models/weighting", headers=headers, json={"mode": "auto"})
    assert resp.json()["weighting_mode"] == "auto"
    weights = {m["method"]: m["display_weight"] for m in resp.json()["models"]}
    assert weights["elo"] > weights["xg"]
    assert abs((weights["elo"] + weights["xg"]) - 100.0) < 0.1


# --- promote / demote + override -------------------------------------------


@pytest.mark.asyncio
async def test_promote_below_min_samples_warns_and_flags_override(
    client: AsyncClient, session: AsyncSession
) -> None:
    weak = await _seed_model(session, method="xg", accuracy=40, sample_count=50)  # < min 300
    await session.commit()
    headers = await _admin_headers(session)

    resp = await client.post(f"/admin/models/{weak}/promote", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"promoted": True, "warning": "below_min_samples"}

    events = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "model.champion.promoted")
            )
        )
        .scalars()
        .all()
    )
    assert events[0].meta["override"] is True


@pytest.mark.asyncio
async def test_promote_demote_snapshot_and_rollback(
    client: AsyncClient, session: AsyncSession
) -> None:
    champ = await _seed_model(session, method="elo", accuracy=65, status=ModelStatus.champion)
    challenger = await _seed_model(session, method="xg", accuracy=60)
    await session.commit()
    headers = await _admin_headers(session)

    # Promote the challenger → snapshot taken, previous champion demoted.
    promoted = await client.post(f"/admin/models/{challenger}/promote", headers=headers)
    assert promoted.json()["warning"] is None  # qualified

    snaps = (await client.get("/admin/models/snapshots", headers=headers)).json()
    assert len(snaps) >= 1
    snap_id = snaps[0]["id"]

    diff = (await client.get(f"/admin/models/snapshots/{snap_id}/diff", headers=headers)).json()
    methods_changed = {c["method"] for c in diff["changes"]}
    assert {"elo", "xg"} & methods_changed  # a champion change is previewed
    # Diff carries status, weight, enabled and visible before→after.
    assert all(
        {"status_to", "weight_to", "enabled_to", "visible_to"} <= c.keys() for c in diff["changes"]
    )

    # Roll back → the original champion (elo) is restored.
    assert (
        await client.post(f"/admin/models/rollback/{snap_id}", headers=headers)
    ).status_code == 204

    session.expire_all()
    champ_row = await session.get(ModelRegistry, champ)
    challenger_row = await session.get(ModelRegistry, challenger)
    assert champ_row is not None and champ_row.status == ModelStatus.champion
    assert challenger_row is not None and challenger_row.status == ModelStatus.challenger


@pytest.mark.asyncio
async def test_retrain_enqueues(client: AsyncClient, session: AsyncSession) -> None:
    fake = _FakePool()
    app.dependency_overrides[get_arq_pool] = lambda: fake
    try:
        headers = await _admin_headers(session)
        resp = await client.post("/admin/models/retrain", headers=headers)
        assert resp.status_code == 202
    finally:
        app.dependency_overrides.pop(get_arq_pool, None)
    assert fake.jobs == ["train_all_task"]
