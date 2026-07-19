"""Phase 13b sensitive endpoint rate limits."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from pytest import MonkeyPatch

from app.core.config import get_settings


async def test_llm_analysis_rate_limit_returns_429(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_llm_analysis_per_minute", 1)

    missing_fixture = uuid.uuid4()
    first = await client.get(f"/matches/{missing_fixture}/analysis")
    second = await client.get(f"/matches/{missing_fixture}/analysis")

    assert first.status_code == 404
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many LLM analysis requests"
    assert "Retry-After" in second.headers


async def test_admin_mutation_limit_uses_forwarded_client_ip(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_admin_mutation_per_minute", 1)

    first = await client.post(
        "/admin/providers",
        json={"name": "x", "roles": []},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    other_client = await client.post(
        "/admin/providers",
        json={"name": "x", "roles": []},
        headers={"X-Forwarded-For": "203.0.113.11"},
    )
    repeated = await client.post(
        "/admin/providers",
        json={"name": "x", "roles": []},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )

    assert first.status_code == 401
    assert other_client.status_code == 401
    assert repeated.status_code == 429


async def test_admin_mutation_rate_limit_preserves_cors_and_security_headers(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_admin_mutation_per_minute", 1)
    headers = {
        "Origin": "http://localhost:3000",
        "X-Forwarded-For": "203.0.113.20",
    }

    first = await client.post("/admin/providers", json={"name": "x", "roles": []}, headers=headers)
    second = await client.post("/admin/providers", json={"name": "x", "roles": []}, headers=headers)

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many admin mutation attempts"
    assert "Retry-After" in second.headers
    assert second.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert second.headers["access-control-expose-headers"] == "Retry-After"
    assert second.headers["x-content-type-options"] == "nosniff"
