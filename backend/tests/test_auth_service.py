"""Service-level tests: lockout backoff, refresh rotation, reuse detection."""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import auth as svc
from app.services.audit import AuditAction
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD = "correct horse battery staple"


async def _make_user(session: AsyncSession, email: str = "u@example.com") -> User:
    user = await svc.register_user(session, email=email, password=PASSWORD)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_wrong_password_locks_account_with_backoff(session: AsyncSession) -> None:
    settings = get_settings()
    await _make_user(session)

    for _ in range(settings.login_max_failures):
        with pytest.raises(svc.InvalidCredentials):
            await svc.authenticate(session, email="u@example.com", password="nope")

    # Threshold reached → account is now temporarily locked (not permanently).
    with pytest.raises(svc.AccountLocked) as exc:
        await svc.authenticate(session, email="u@example.com", password=PASSWORD)
    assert exc.value.retry_after > 0

    # Failed logins are audited.
    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == AuditAction.LOGIN_FAILURE)
    )
    assert count == settings.login_max_failures


@pytest.mark.asyncio
async def test_login_does_not_reveal_unknown_email(session: AsyncSession) -> None:
    with pytest.raises(svc.InvalidCredentials):
        await svc.authenticate(session, email="ghost@example.com", password="whatever")
    # A failure for an unknown email is still audited (actor is null).
    row = await session.scalar(select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILURE))
    assert row is not None
    assert row.actor_user_id is None


@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse_detection(session: AsyncSession) -> None:
    user = await _make_user(session)
    tokens1 = await svc.issue_token_pair(session, user)

    tokens2 = await svc.rotate_refresh_token(session, refresh_token=tokens1.refresh_token)
    assert tokens2.refresh_token != tokens1.refresh_token

    # Replaying the already-rotated token trips reuse detection.
    with pytest.raises(svc.TokenReuseDetected):
        await svc.rotate_refresh_token(session, refresh_token=tokens1.refresh_token)

    # The whole family is now revoked, so the fresh token is dead too — and
    # using that revoked token is itself flagged as reuse.
    with pytest.raises(svc.TokenReuseDetected):
        await svc.rotate_refresh_token(session, refresh_token=tokens2.refresh_token)

    assert (
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.TOKEN_REUSE_DETECTED)
        )
        == 2
    )


@pytest.mark.asyncio
async def test_email_verification_token_single_use(session: AsyncSession) -> None:
    user = await _make_user(session)
    token = await svc.create_email_verification_token(session, user)

    verified = await svc.verify_email(session, token=token)
    assert verified.is_verified is True

    with pytest.raises(svc.InvalidToken):
        await svc.verify_email(session, token=token)


@pytest.mark.asyncio
async def test_change_password_revokes_refresh_tokens(session: AsyncSession) -> None:
    user = await _make_user(session)
    tokens = await svc.issue_token_pair(session, user)

    await svc.change_password(
        session, user=user, current_password=PASSWORD, new_password="a-brand-new-pass-9"
    )

    with pytest.raises(svc.AuthError):
        await svc.rotate_refresh_token(session, refresh_token=tokens.refresh_token)

    with pytest.raises(svc.InvalidCredentials):
        await svc.change_password(
            session, user=user, current_password="wrong", new_password="another-pass-99"
        )
