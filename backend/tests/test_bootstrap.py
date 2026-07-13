"""Tests for the admin bootstrap command."""

from __future__ import annotations

import pytest
from app.bootstrap import _create_admin
from app.models.user import User, UserRole
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_create_admin_is_gated_and_idempotent(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    # First run creates the admin and prints a generated one-time password.
    assert await _create_admin(force=False) == 0
    out = capsys.readouterr().out
    assert "one-time password" in out.lower()

    admin = await session.scalar(select(User).where(User.role == UserRole.admin))
    assert admin is not None
    assert admin.must_change_password is True
    assert admin.is_verified is True

    # Re-running without --force refuses (the email already exists).
    assert await _create_admin(force=False) == 1

    # With --force it resets the existing account.
    assert await _create_admin(force=True) == 0


@pytest.mark.asyncio
async def test_create_admin_uses_configured_password(
    session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "admin_password", "a-configured-admin-pass-1")

    assert await _create_admin(force=False) == 0
    out = capsys.readouterr().out
    # A configured password must never be echoed back.
    assert "a-configured-admin-pass-1" not in out
    assert "one-time password" not in out.lower()
