"""Admin ingestion job-log + re-scan: RBAC, audit, enqueue, validation (Phase 12a)."""

from __future__ import annotations

import uuid

import pytest
from app.core.arq import get_arq_pool
from app.core.security import create_access_token
from app.main import app
from app.models.audit_log import AuditLog
from app.models.ingestion_run import IngestionRun, IngestionStatus
from app.models.user import User, UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class _FakePool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, name: str, *args: object, **_kwargs: object) -> None:
        self.jobs.append((name, args))


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


@pytest.mark.asyncio
async def test_runs_list_paginates_and_filters(client: AsyncClient, session: AsyncSession) -> None:
    session.add_all(
        [
            IngestionRun(
                provider="fd", league="EPL", season="2023-2024", status=IngestionStatus.success
            ),
            IngestionRun(
                provider="fd", league="EPL", season="2024-2025", status=IngestionStatus.running
            ),
        ]
    )
    await session.commit()
    headers = await _admin_headers(session)

    all_runs = await client.get("/admin/ingestion/runs", headers=headers)
    assert all_runs.status_code == 200
    assert all_runs.json()["total"] == 2

    running = await client.get("/admin/ingestion/runs?status=running", headers=headers)
    body = running.json()
    assert body["total"] == 1
    assert body["runs"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_rescan_enqueues_and_audits(client: AsyncClient, session: AsyncSession) -> None:
    fake = _FakePool()
    app.dependency_overrides[get_arq_pool] = lambda: fake
    try:
        headers = await _admin_headers(session)
        resp = await client.post(
            "/admin/ingestion/rescan",
            headers=headers,
            json={"leagues": ["EPL"], "seasons": ["2023-2024"]},
        )
        assert resp.status_code == 202
        assert resp.json()["accepted"] is True
    finally:
        app.dependency_overrides.pop(get_arq_pool, None)

    name, args = fake.jobs[0]
    assert name == "ingest_history_task"
    assert args[0] == ["EPL"] and args[1] == ["2023-2024"]
    assert str(args[2]).startswith("admin:")

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "ingestion.rescan")))
        .scalars()
        .all()
    )
    assert len(events) == 1 and events[0].meta["leagues"] == ["EPL"]


@pytest.mark.asyncio
async def test_rescan_rejects_unknown_league(client: AsyncClient, session: AsyncSession) -> None:
    fake = _FakePool()
    app.dependency_overrides[get_arq_pool] = lambda: fake
    try:
        headers = await _admin_headers(session)
        resp = await client.post(
            "/admin/ingestion/rescan",
            headers=headers,
            json={"leagues": ["ATLANTIS"], "seasons": ["2023-2024"]},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "unknown_leagues"
    finally:
        app.dependency_overrides.pop(get_arq_pool, None)
    assert fake.jobs == []  # nothing enqueued on a validation failure


@pytest.mark.asyncio
async def test_ingestion_endpoints_require_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _user_headers(session)
    assert (await client.get("/admin/ingestion/runs", headers=headers)).status_code == 403
    assert (
        await client.post(
            "/admin/ingestion/rescan",
            headers=headers,
            json={"leagues": ["EPL"], "seasons": ["2023-2024"]},
        )
    ).status_code == 403
