"""Phase 13b sensitive endpoint rate limits."""

from __future__ import annotations

import uuid

from app.core.config import get_settings
from httpx import AsyncClient
from pytest import MonkeyPatch


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


async def test_admin_mutation_rate_limit_returns_429(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_admin_mutation_per_minute", 1)

    first = await client.post("/admin/providers", json={"name": "x", "roles": []})
    second = await client.post("/admin/providers", json={"name": "x", "roles": []})

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many admin mutation attempts"
    assert "Retry-After" in second.headers
    assert second.headers["x-content-type-options"] == "nosniff"
