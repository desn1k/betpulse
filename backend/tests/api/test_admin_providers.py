"""Admin provider management: RBAC, audit, masked write-only API key (Phase 12a)."""

from __future__ import annotations

import uuid

import pytest
from app.core.security import create_access_token
from app.models.audit_log import AuditLog
from app.models.reference import ProviderAccount
from app.models.user import User, UserRole
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


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
async def test_create_masks_key_and_audits(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _admin_headers(session)
    resp = await client.post(
        "/admin/providers",
        headers=headers,
        json={
            "name": "api_football",
            "roles": ["live", "odds"],
            "priority": 10,
            "api_key": "dummy-key-wxyz9",
            "requests_per_day": 7500,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["key_masked"] == "••••xyz9"
    assert body["roles"] == ["live", "odds"]
    assert "encrypted_key" not in body
    assert "dummy-key-wxyz9" not in resp.text

    # Stored encrypted, not plaintext.
    provider = (
        await session.execute(select(ProviderAccount).where(ProviderAccount.name == "api_football"))
    ).scalar_one()
    assert provider.encrypted_key is not None and "dummy-key-wxyz9" not in provider.encrypted_key
    assert provider.key_suffix == "xyz9"

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "provider.create")))
        .scalars()
        .all()
    )
    assert len(events) == 1 and events[0].target == "provider:api_football"


@pytest.mark.asyncio
async def test_update_rotates_key_and_lists_masked(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _admin_headers(session)
    created = (
        await client.post(
            "/admin/providers",
            headers=headers,
            json={"name": "p1", "roles": ["live"], "api_key": "dummy-old-1111"},
        )
    ).json()

    patched = await client.patch(
        f"/admin/providers/{created['id']}",
        headers=headers,
        json={"api_key": "dummy-new-2222", "priority": 5},
    )
    assert patched.status_code == 200
    assert patched.json()["key_masked"] == "••••2222"

    listed = await client.get("/admin/providers", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["key_masked"] == "••••2222"
    assert "encrypted_key" not in listed.text

    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "provider.update")))
        .scalars()
        .all()
    )
    assert "api_key" in events[0].meta["fields"]


@pytest.mark.asyncio
async def test_enable_disable_and_delete(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _admin_headers(session)
    pid = (
        await client.post(
            "/admin/providers", headers=headers, json={"name": "p2", "roles": ["odds"]}
        )
    ).json()["id"]

    assert (await client.post(f"/admin/providers/{pid}/disable", headers=headers)).json()[
        "is_enabled"
    ] is False
    assert (await client.post(f"/admin/providers/{pid}/enable", headers=headers)).json()[
        "is_enabled"
    ] is True

    assert (await client.delete(f"/admin/providers/{pid}", headers=headers)).status_code == 204
    assert await session.get(ProviderAccount, uuid.UUID(pid)) is None


@pytest.mark.asyncio
async def test_provider_endpoints_require_admin(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _user_headers(session)
    assert (await client.get("/admin/providers", headers=headers)).status_code == 403
    assert (
        await client.post("/admin/providers", headers=headers, json={"name": "x", "roles": []})
    ).status_code == 403
    assert (
        await client.patch(
            f"/admin/providers/{uuid.uuid4()}", headers=headers, json={"priority": 1}
        )
    ).status_code == 403
    assert (
        await client.delete(f"/admin/providers/{uuid.uuid4()}", headers=headers)
    ).status_code == 403
