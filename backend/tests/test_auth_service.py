"""Service-level tests: lockout backoff, refresh rotation, reuse detection."""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import auth as svc
from app.services.audit import AuditAction
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD = "correct horse battery staple"


async def _make_user(session: AsyncSession, email: str = "u@example.com") -> User:
    # Committed: failure-path security state is written in a transaction of
    # its own, which must be able to see (and update) the user row.
    user = await svc.register_user(session, email=email, password=PASSWORD)
    await session.commit()
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
async def test_failed_login_commit_leaves_caller_session_alone(session: AsyncSession) -> None:
    """The pre-raise commit persists only security state: the caller's pending
    and flushed-but-uncommitted changes stay uncommitted, and its session (and
    the user object it holds) remain usable afterwards."""
    user = await _make_user(session)
    session.add(AuditLog(action="test.unrelated.flushed", meta={}))
    await session.flush()
    session.add(AuditLog(action="test.unrelated.pending", meta={}))

    with pytest.raises(svc.InvalidCredentials):
        await svc.authenticate(session, email="u@example.com", password="nope")

    # The caller's session is still usable and sees the updated counter.
    assert user.email == "u@example.com"
    assert user.failed_login_count == 1
    assert user not in session.dirty
    assert await session.scalar(select(func.count()).select_from(User)) == 1

    async with _write_sessionmaker()() as other:
        actions = set((await other.scalars(select(AuditLog.action))).all())
        persisted = await other.scalar(select(User.failed_login_count).where(User.id == user.id))
    assert persisted == 1
    assert AuditAction.LOGIN_FAILURE in actions
    assert "test.unrelated.flushed" not in actions
    assert "test.unrelated.pending" not in actions

    # The caller decides the fate of its own changes.
    await session.rollback()
    async with _write_sessionmaker()() as other:
        assert (
            await other.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action.like("test.unrelated.%"))
            )
            == 0
        )


@pytest.mark.asyncio
async def test_login_does_not_reveal_unknown_email(session: AsyncSession) -> None:
    with pytest.raises(svc.InvalidCredentials):
        await svc.authenticate(session, email="ghost@example.com", password="whatever")
    # A failure for an unknown email is still audited (actor is null), but the
    # address itself is not stored — only a keyed fingerprint of it.
    row = await session.scalar(select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILURE))
    assert row is not None
    assert row.actor_user_id is None
    assert row.target is not None
    assert row.target.startswith("email-hmac:")
    assert "ghost" not in row.target


@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse_detection(session: AsyncSession) -> None:
    user = await _make_user(session)
    tokens1 = await svc.issue_token_pair(session, user)
    await session.commit()

    tokens2 = await svc.rotate_refresh_token(refresh_token=tokens1.refresh_token)
    assert tokens2.refresh_token != tokens1.refresh_token

    # Presented again right away it is a benign duplicate: nothing issued,
    # nothing revoked.
    with pytest.raises(svc.RefreshConflict):
        await svc.rotate_refresh_token(refresh_token=tokens1.refresh_token)

    # Outside the grace window the same replay trips reuse detection.
    await session.execute(
        update(RefreshToken).values(created_at=func.now() - text("interval '1 hour'"))
    )
    await session.commit()
    with pytest.raises(svc.TokenReuseDetected):
        await svc.rotate_refresh_token(refresh_token=tokens1.refresh_token)

    # The whole family is now revoked, so the fresh token is dead too — and
    # using that revoked token is itself flagged as reuse.
    with pytest.raises(svc.TokenReuseDetected):
        await svc.rotate_refresh_token(refresh_token=tokens2.refresh_token)

    assert (
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.TOKEN_REUSE_DETECTED)
        )
        == 2
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.TOKEN_REFRESH_CONFLICT)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_unknown_refresh_token_is_invalid() -> None:
    with pytest.raises(svc.InvalidToken):
        await svc.rotate_refresh_token(refresh_token="not-a-real-token")


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
    await session.commit()

    with pytest.raises(svc.AuthError):
        await svc.rotate_refresh_token(refresh_token=tokens.refresh_token)

    with pytest.raises(svc.InvalidCredentials):
        await svc.change_password(
            session, user=user, current_password="wrong", new_password="another-pass-99"
        )
