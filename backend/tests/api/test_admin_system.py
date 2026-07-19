"""Admin system health, audit viewer and ops alerts (Phase 12d)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import create_access_token
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.services.audit import record_event
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
async def test_system_endpoints_require_admin(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _user_headers(session)
    assert (await client.get("/admin/system/health", headers=headers)).status_code == 403
    assert (await client.get("/admin/audit", headers=headers)).status_code == 403
    assert (
        await client.post("/admin/system/alerts/test", headers=headers, json={"message": "x"})
    ).status_code == 403


@pytest.mark.asyncio
async def test_system_health_reports_components(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _admin_headers(session)
    resp = await client.get("/admin/system/health", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    components = {c["name"]: c for c in body["components"]}
    assert components["postgres"]["status"] == "ok"
    assert components["redis"]["status"] == "ok"
    assert components["ops_alerts"]["status"] == "not_configured"


@pytest.mark.asyncio
async def test_audit_list_filters_and_paginates(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _admin_headers(session)
    actor = (await session.execute(select(User).where(User.role == UserRole.admin))).scalar_one()
    await record_event(
        session,
        action="provider.create",
        actor_user_id=actor.id,
        target="provider:api_football",
        meta={"roles": ["live"]},
    )
    await record_event(session, action="user.disable", target="user:abc", meta={"revoked": 2})
    await session.commit()

    resp = await client.get(
        "/admin/audit?action=provider.create&page=1&per_page=10", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["events"][0]["action"] == "provider.create"
    assert body["events"][0]["actor_email"] == actor.email
    assert body["events"][0]["meta"] == {"roles": ["live"]}

    q = await client.get("/admin/audit?q=disable", headers=headers)
    assert q.status_code == 200
    assert q.json()["events"][0]["action"] == "user.disable"


@pytest.mark.asyncio
async def test_audit_date_filters(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _admin_headers(session)
    now = datetime.now(UTC)
    old = AuditLog(action="old.event", created_at=now - timedelta(days=2))
    fresh = AuditLog(action="fresh.event", created_at=now)
    session.add_all([old, fresh])
    await session.commit()

    resp = await client.get(
        f"/admin/audit?date_from={(now - timedelta(hours=1)).isoformat()}", headers=headers
    )
    assert resp.status_code == 200
    actions = {e["action"] for e in resp.json()["events"]}
    assert "fresh.event" in actions
    assert "old.event" not in actions


@pytest.mark.asyncio
async def test_audit_pagination_uses_stable_id_tie_breaker(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _admin_headers(session)
    same_time = datetime(2026, 7, 19, tzinfo=UTC)
    low_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    high_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    session.add_all(
        [
            AuditLog(id=low_id, action="same.low", created_at=same_time),
            AuditLog(id=high_id, action="same.high", created_at=same_time),
        ]
    )
    await session.commit()

    first = (await client.get("/admin/audit?per_page=1&page=1", headers=headers)).json()
    second = (await client.get("/admin/audit?per_page=1&page=2", headers=headers)).json()
    assert first["events"][0]["id"] == str(high_id)
    assert second["events"][0]["id"] == str(low_id)


@pytest.mark.asyncio
async def test_ops_alert_not_configured_does_not_audit(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _admin_headers(session)
    resp = await client.post(
        "/admin/system/alerts/test", headers=headers, json={"message": "hello"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_configured"
    events = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "ops_alert.test")))
        .scalars()
        .all()
    )
    assert events == []


@pytest.mark.asyncio
async def test_ops_alert_send_is_audited(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import Settings, get_settings
    from app.services import ops_alerts

    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", "token")
    monkeypatch.setattr(settings, "telegram_alert_chat_id", "chat")

    sent: list[str] = []

    async def fake_send(settings: Settings, message: str) -> None:
        sent.append(message)

    monkeypatch.setattr(ops_alerts, "send_ops_alert", fake_send)
    headers = await _admin_headers(session)
    resp = await client.post(
        "/admin/system/alerts/test", headers=headers, json={"message": "Phase 12d smoke"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "sent", "detail": None}
    assert sent == ["Phase 12d smoke"]
    event = (
        await session.execute(select(AuditLog).where(AuditLog.action == "ops_alert.test"))
    ).scalar_one()
    assert event.meta == {"message_length": 16}


@pytest.mark.asyncio
async def test_ops_alert_transport_error_becomes_delivery_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from app.core.config import Settings
    from app.services import ops_alerts

    class FailingClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, str]) -> object:
            import httpx

            raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)
    settings = Settings(telegram_bot_token="token", telegram_alert_chat_id="chat")
    with pytest.raises(ops_alerts.OpsAlertDeliveryFailed):
        await ops_alerts.send_ops_alert(settings, "hello")
