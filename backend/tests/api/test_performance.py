"""Public /performance endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.models.model_registry import ModelRegistry, ModelStatus
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_no_evaluation_yet(client: AsyncClient, session: AsyncSession) -> None:
    session.add(
        ModelRegistry(
            method="elo",
            version="v1",
            status=ModelStatus.challenger,
            is_enabled=True,
            is_visible=True,
            display_weight=Decimal("0"),
            sample_count=0,
        )
    )
    await session.commit()

    resp = await client.get("/performance")
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_evaluation_yet"}


@pytest.mark.asyncio
async def test_performance_reports_champion(client: AsyncClient, session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            ModelRegistry(
                method="dixon_coles",
                version="v1",
                status=ModelStatus.champion,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("60"),
                accuracy_pct=Decimal("12.50"),
                brier=Decimal("0.180000"),
                log_loss=Decimal("0.900000"),
                roi_vs_closing=Decimal("2.5"),
                sample_count=400,
                last_evaluated_at=now,
            ),
            ModelRegistry(
                method="elo",
                version="v1",
                status=ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("40"),
                accuracy_pct=Decimal("8.00"),
                sample_count=400,
                last_evaluated_at=now,
            ),
        ]
    )
    await session.commit()

    body = (await client.get("/performance")).json()
    assert body["status"] == "ok"
    assert body["champion"] == "dixon_coles"
    assert body["evaluated_at"] is not None
    methods = {m["method"]: m for m in body["methods"]}
    assert methods["dixon_coles"]["is_champion"] is True
    assert methods["dixon_coles"]["accuracy_pct"] == 12.5


@pytest.mark.asyncio
async def test_performance_requires_no_auth(client: AsyncClient, session: AsyncSession) -> None:
    # Endpoint is reachable with no Authorization header.
    assert (await client.get("/performance")).status_code == 200
