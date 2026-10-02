"""Authentication service.

Owns account creation, credential verification (with per-account exponential
backoff, anti-enumeration timing, and optional TOTP), refresh-token rotation
with family-wide reuse detection, email-verification tokens, and password
change. All state changes are audited — failures included.

Transaction rule: security state written on a failure path (failed-login
counter, lockout, family revocation, their audit rows) is committed in its own
short transaction (:func:`_commit_security_state`) *before* the domain error is
raised, because the request transaction is rolled back on any exception —
including the ``HTTPException`` a router maps the error to. Refresh rotation
runs entirely in such a transaction, serialised per token family.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.db import independent_transaction
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


class RefreshConflict(AuthError):
    """The token was rotated moments ago by a concurrent request (double-click,
    second tab). Nothing is issued and the family stays live; the client retries
    with the cookie the winning request set."""


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


@asynccontextmanager
async def _commit_security_state() -> AsyncIterator[AsyncSession]:
    """Write security state that must outlive a failed request. Committed when
    the block exits — before the caller raises — in a transaction of its own, so
    the caller's session is neither committed nor otherwise touched."""
    async with independent_transaction() as security_session:
        yield security_session


def _email_fingerprint(normalized_email: str) -> str:
    """Keyed, truncated digest of an address that is not a known active account:
    audit can correlate repeated attempts without storing the address itself."""
    key = get_settings().secret_key.encode("utf-8")
    digest = hmac.new(key, normalized_email.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"email-hmac:{digest[:16]}"


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


async def _register_failed_attempt(
    user: User,
    *,
    action: str,
    target: str,
    ip: str | None,
    user_agent: str | None,
    reason: str,
) -> None:
    """Atomically bump the failure counter (no read-modify-write), set the
    backoff lock once past the threshold, audit, and commit — all before the
    caller raises. ``user`` is updated in place without being marked dirty."""
    async with _commit_security_state() as security_session:
        failed_count = (
            await security_session.execute(
                update(User)
                .where(User.id == user.id)
                .values(failed_login_count=User.failed_login_count + 1)
                .returning(User.failed_login_count)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one()
        locked_until = user.locked_until
        seconds = _lockout_seconds(failed_count)
        if seconds > 0:
            locked_until = _now() + timedelta(seconds=seconds)
            await security_session.execute(
                update(User)
                .where(User.id == user.id)
                .values(locked_until=locked_until)
                .execution_options(synchronize_session=False)
            )
        await record_event(
            security_session,
            action=action,
            actor_user_id=user.id,
            target=target,
            ip=ip,
            user_agent=user_agent,
            meta={"reason": reason, "failed_count": failed_count},
        )
    set_committed_value(user, "failed_login_count", failed_count)
    set_committed_value(user, "locked_until", locked_until)


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    totp_code: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Verify credentials. Failure-path state is committed independently of
    ``session`` (see the module docstring), so the caller must not hold
    uncommitted writes on this user's row."""
    normalized = _normalize_email(email)
    user = await get_user_by_email(session, normalized)

    if user is None or not user.is_active:
        # Equalise timing and audit without revealing whether the email exists.
        # The address may belong to no one, so only its fingerprint is kept.
        verify_dummy_password()
        async with _commit_security_state() as security_session:
            await record_event(
                security_session,
                action=AuditAction.LOGIN_FAILURE,
                actor_user_id=user.id if user else None,
                target=_email_fingerprint(normalized),
                ip=ip,
                user_agent=user_agent,
                meta={"reason": "unknown_or_inactive"},
            )
        raise InvalidCredentials

    now = _now()
    if user.locked_until is not None and user.locked_until > now:
        retry_after = max(1, int((user.locked_until - now).total_seconds()))
        async with _commit_security_state() as security_session:
            await record_event(
                security_session,
                action=AuditAction.LOGIN_LOCKED,
                actor_user_id=user.id,
                target=normalized,
                ip=ip,
                user_agent=user_agent,
                meta={"retry_after": retry_after},
            )
        raise AccountLocked(retry_after=retry_after)

    if not verify_password(user.password_hash, password):
        await _register_failed_attempt(
            user,
            action=AuditAction.LOGIN_FAILURE,
            target=normalized,
            ip=ip,
            user_agent=user_agent,
            reason="bad_password",
        )
        raise InvalidCredentials

    if user.totp_enabled:
        if not totp_code:
            raise TwoFactorRequired
        secret = decrypt_secret(user.totp_secret_encrypted or "")
        if not verify_totp(secret, totp_code):
            await _register_failed_attempt(
                user,
                action=AuditAction.TWOFA_FAILURE,
                target=normalized,
                ip=ip,
                user_agent=user_agent,
                reason="bad_totp",
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


async def _store_refresh(
    session: AsyncSession, *, user: User, family_id: uuid.UUID
) -> tuple[str, RefreshToken]:
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
    return plain, row


async def issue_token_pair(session: AsyncSession, user: User) -> IssuedTokens:
    access, expires_in = _access_for(user)
    refresh, _ = await _store_refresh(session, user=user, family_id=uuid.uuid4())
    return IssuedTokens(
        access_token=access,
        expires_in=expires_in,
        refresh_token=refresh,
        csrf_token=generate_csrf_token(),
        user=user,
    )


async def _lock_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    """Serialise every rotation/revocation of one token family until commit.

    A row lock on the presented token alone is not enough: revoking a family in
    READ COMMITTED would miss a descendant that a concurrent rotation inserted
    but had not yet committed, leaving it live."""
    key = int.from_bytes(family_id.bytes[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    await session.flush()


async def _is_benign_duplicate(session: AsyncSession, row: RefreshToken) -> bool:
    """True when ``row`` was rotated within the grace window and its direct
    replacement is still the family's live token — i.e. a concurrent duplicate
    of a legitimate refresh rather than a replay. Timed on the DB clock."""
    if row.replaced_by is None:
        return False
    child = await session.get(RefreshToken, row.replaced_by)
    if child is None or child.revoked or child.replaced_by is not None:
        return False
    db_now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    grace = timedelta(seconds=get_settings().refresh_reuse_grace_seconds)
    return bool(db_now - child.created_at <= grace)


async def _rotate_locked(
    session: AsyncSession,
    token_hash: str,
    *,
    ip: str | None,
    user_agent: str | None,
) -> IssuedTokens | AuthError:
    """Decide and apply one rotation; errors are returned, not raised, so the
    caller can commit their security state first."""
    family_id = await session.scalar(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == token_hash)
    )
    if family_id is None:
        return InvalidToken()
    await _lock_family(session, family_id)
    row = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    if row.revoked or row.replaced_by is not None:
        meta: dict[str, Any] = {"family_id": str(row.family_id)}
        if await _is_benign_duplicate(session, row):
            await record_event(
                session,
                action=AuditAction.TOKEN_REFRESH_CONFLICT,
                actor_user_id=row.user_id,
                ip=ip,
                user_agent=user_agent,
                meta=meta,
            )
            return RefreshConflict()
        # A previously rotated/revoked token was replayed → revoke the family.
        await _revoke_family(session, row.family_id)
        await record_event(
            session,
            action=AuditAction.TOKEN_REUSE_DETECTED,
            actor_user_id=row.user_id,
            ip=ip,
            user_agent=user_agent,
            meta=meta,
        )
        return TokenReuseDetected()

    if row.expires_at <= _now():
        return InvalidToken()

    user = await get_user_by_id(session, row.user_id)
    if user is None or not user.is_active:
        return InvalidToken()

    new_plain, new_row = await _store_refresh(session, user=user, family_id=row.family_id)
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


async def rotate_refresh_token(
    *,
    refresh_token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> IssuedTokens:
    """Rotate ``refresh_token`` atomically in a transaction of its own.

    Concurrent presentations of one token serialise on its family: exactly one
    rotates it. A loser arriving within ``refresh_reuse_grace_seconds`` gets
    :class:`RefreshConflict` (nothing issued, family intact); a replay outside
    the window, or of a revoked token, revokes the whole family and raises
    :class:`TokenReuseDetected`. Every outcome is committed before returning or
    raising, so revocations and their audit rows persist.
    """
    async with _commit_security_state() as security_session:
        outcome = await _rotate_locked(
            security_session, hash_token(refresh_token), ip=ip, user_agent=user_agent
        )
    if isinstance(outcome, AuthError):
        raise outcome
    return outcome


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
    await _lock_family(session, row.family_id)
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
