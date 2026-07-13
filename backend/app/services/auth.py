"""Authentication service.

Owns account creation, credential verification (with per-account exponential
backoff, anti-enumeration timing, and optional TOTP), refresh-token rotation
with family-wide reuse detection, email-verification tokens, and password
change. All state changes are audited — failures included.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.security import (
    create_access_token,
    generate_csrf_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_dummy_password,
    verify_password,
    verify_totp,
)
from app.models.email_verification_token import EmailVerificationToken
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.services.audit import AuditAction, record_event


class AuthError(Exception):
    """Base class for authentication errors."""


class EmailAlreadyRegistered(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


class AccountLocked(AuthError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("account temporarily locked")


class TwoFactorRequired(AuthError):
    pass


class TwoFactorInvalid(AuthError):
    pass


class InvalidToken(AuthError):
    pass


class TokenReuseDetected(AuthError):
    pass


@dataclass(slots=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str  # plaintext, to be set as an httpOnly cookie by the router
    csrf_token: str
    user: User


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _now() -> datetime:
    return datetime.now(UTC)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == _normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    normalized = _normalize_email(email)
    if await get_user_by_email(session, normalized) is not None:
        raise EmailAlreadyRegistered(normalized)

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        role=UserRole.user,
        is_verified=False,
    )
    session.add(user)
    await session.flush()
    await record_event(
        session,
        action=AuditAction.REGISTER,
        actor_user_id=user.id,
        target=normalized,
        ip=ip,
        user_agent=user_agent,
    )
    return user


def _lockout_seconds(failed_count: int) -> int:
    """Exponential backoff after the failure threshold; never a permanent lock."""
    settings = get_settings()
    if failed_count < settings.login_max_failures:
        return 0
    over = failed_count - settings.login_max_failures
    backoff = settings.lockout_base_seconds * (2**over)
    return int(min(backoff, settings.lockout_max_seconds))


async def _register_failed_attempt(session: AsyncSession, user: User) -> None:
    user.failed_login_count += 1
    seconds = _lockout_seconds(user.failed_login_count)
    if seconds > 0:
        user.locked_until = _now() + timedelta(seconds=seconds)
    await session.flush()


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    totp_code: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    normalized = _normalize_email(email)
    user = await get_user_by_email(session, normalized)

    if user is None or not user.is_active:
        # Equalise timing and audit without revealing whether the email exists.
        verify_dummy_password()
        await record_event(
            session,
            action=AuditAction.LOGIN_FAILURE,
            actor_user_id=user.id if user else None,
            target=normalized,
            ip=ip,
            user_agent=user_agent,
            meta={"reason": "unknown_or_inactive"},
        )
        raise InvalidCredentials

    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        retry_after = int((user.locked_until - now).total_seconds())
        await record_event(
            session,
            action=AuditAction.LOGIN_LOCKED,
            actor_user_id=user.id,
            target=normalized,
            ip=ip,
            user_agent=user_agent,
            meta={"retry_after": retry_after},
        )
        raise AccountLocked(retry_after=retry_after)

    if not verify_password(user.password_hash, password):
        await _register_failed_attempt(session, user)
        await record_event(
            session,
            action=AuditAction.LOGIN_FAILURE,
            actor_user_id=user.id,
            target=normalized,
            ip=ip,
            user_agent=user_agent,
            meta={"reason": "bad_password", "failed_count": user.failed_login_count},
        )
        raise InvalidCredentials

    if user.totp_enabled:
        if not totp_code:
            raise TwoFactorRequired
        secret = decrypt_secret(user.totp_secret_encrypted or "")
        if not verify_totp(secret, totp_code):
            await _register_failed_attempt(session, user)
            await record_event(
                session,
                action=AuditAction.TWOFA_FAILURE,
                actor_user_id=user.id,
                target=normalized,
                ip=ip,
                user_agent=user_agent,
            )
            raise TwoFactorInvalid

    # Success: reset lockout state and upgrade the hash if parameters changed.
    user.failed_login_count = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    await record_event(
        session,
        action=AuditAction.LOGIN_SUCCESS,
        actor_user_id=user.id,
        target=normalized,
        ip=ip,
        user_agent=user_agent,
    )
    await session.flush()
    return user


def _access_for(user: User) -> tuple[str, int]:
    settings = get_settings()
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return token, settings.jwt_access_ttl_minutes * 60


async def _store_refresh(session: AsyncSession, *, user: User, family_id: uuid.UUID) -> str:
    settings = get_settings()
    plain = generate_opaque_token()
    row = RefreshToken(
        user_id=user.id,
        family_id=family_id,
        token_hash=hash_token(plain),
        expires_at=_now() + timedelta(days=settings.jwt_refresh_ttl_days),
    )
    session.add(row)
    await session.flush()
    return plain


async def issue_token_pair(session: AsyncSession, user: User) -> IssuedTokens:
    access, expires_in = _access_for(user)
    refresh = await _store_refresh(session, user=user, family_id=uuid.uuid4())
    return IssuedTokens(
        access_token=access,
        expires_in=expires_in,
        refresh_token=refresh,
        csrf_token=generate_csrf_token(),
        user=user,
    )


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    await session.flush()


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    refresh_token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> IssuedTokens:
    token_hash = hash_token(refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidToken

    if row.revoked or row.replaced_by is not None:
        # A previously rotated/revoked token was replayed → revoke the family.
        await _revoke_family(session, row.family_id)
        await record_event(
            session,
            action=AuditAction.TOKEN_REUSE_DETECTED,
            actor_user_id=row.user_id,
            ip=ip,
            user_agent=user_agent,
            meta={"family_id": str(row.family_id)},
        )
        raise TokenReuseDetected

    if row.expires_at <= _now():
        raise InvalidToken

    user = await get_user_by_id(session, row.user_id)
    if user is None or not user.is_active:
        raise InvalidToken

    new_plain = await _store_refresh(session, user=user, family_id=row.family_id)
    new_hash = hash_token(new_plain)
    new_row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == new_hash))
    ).scalar_one()
    row.revoked = True
    row.replaced_by = new_row.id
    await session.flush()

    access, expires_in = _access_for(user)
    await record_event(
        session,
        action=AuditAction.TOKEN_REFRESH,
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    return IssuedTokens(
        access_token=access,
        expires_in=expires_in,
        refresh_token=new_plain,
        csrf_token=generate_csrf_token(),
        user=user,
    )


async def logout(
    session: AsyncSession,
    *,
    refresh_token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
    )
    row = result.scalar_one_or_none()
    if row is None:
        return
    await _revoke_family(session, row.family_id)
    await record_event(
        session,
        action=AuditAction.LOGOUT,
        actor_user_id=row.user_id,
        ip=ip,
        user_agent=user_agent,
    )


async def revoke_all_user_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    await session.flush()


async def create_email_verification_token(session: AsyncSession, user: User) -> str:
    plain = generate_opaque_token()
    session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_token(plain),
            expires_at=_now() + timedelta(hours=24),
        )
    )
    await session.flush()
    return plain


async def verify_email(
    session: AsyncSession,
    *,
    token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    result = await session.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == hash_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= _now():
        raise InvalidToken

    user = await get_user_by_id(session, row.user_id)
    if user is None:
        raise InvalidToken

    row.used_at = _now()
    user.is_verified = True
    await record_event(
        session,
        action=AuditAction.EMAIL_VERIFIED,
        actor_user_id=user.id,
        target=user.email,
        ip=ip,
        user_agent=user_agent,
    )
    await session.flush()
    return user


async def change_password(
    session: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    if not verify_password(user.password_hash, current_password):
        raise InvalidCredentials
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    await revoke_all_user_tokens(session, user.id)
    await record_event(
        session,
        action=AuditAction.PASSWORD_CHANGED,
        actor_user_id=user.id,
        target=user.email,
        ip=ip,
        user_agent=user_agent,
    )
    await session.flush()
