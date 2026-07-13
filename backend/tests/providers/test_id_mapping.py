"""ID-mapping: strict resolve raises; seed helper creates then resolves."""

from __future__ import annotations

import pytest
from app.providers.id_mapping import (
    UnmappedEntityError,
    get_or_create_canonical_team,
    normalize_name,
    resolve_team,
)
from sqlalchemy.ext.asyncio import AsyncSession


def test_normalize_folds_accents_and_punctuation() -> None:
    assert normalize_name("Nott'm Forest") == "nott m forest"
    assert normalize_name("Atlético Madrid") == "atletico madrid"
    assert normalize_name("  Man   City ") == "man city"


@pytest.mark.asyncio
async def test_resolve_raises_on_unmapped(session: AsyncSession) -> None:
    with pytest.raises(UnmappedEntityError):
        await resolve_team(session, "api_football", "Some Unknown FC")


@pytest.mark.asyncio
async def test_seed_creates_then_resolves(session: AsyncSession) -> None:
    team, created = await get_or_create_canonical_team(
        session, provider="football_data_couk", raw_name="Man City"
    )
    assert created is True

    # Same provider + name resolves to the same team, no new row.
    again, created_again = await get_or_create_canonical_team(
        session, provider="football_data_couk", raw_name="Man City"
    )
    assert created_again is False
    assert again.id == team.id

    resolved = await resolve_team(session, "football_data_couk", "Man City")
    assert resolved.id == team.id
