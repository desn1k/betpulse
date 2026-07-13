"""TOTP two-factor setup / enable / disable.

The TOTP secret is generated on setup and stored **encrypted at rest**; it only
becomes active once the user proves possession by entering a valid code
(``enable``). Admins are required to have 2FA enabled before admin routes unlock
(enforced in the route dependency).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import (
    generate_totp_secret,
    totp_provisioning_uri,
    verify_totp,
)
from app.models.user import User
from app.services.audit import AuditAction, record_event


class TwoFactorError(Exception):
    pass


class InvalidTwoFactorCode(TwoFactorError):
    pass


class TwoFactorNotInitialized(TwoFactorError):
    pass


async def setup_totp(session: AsyncSession, user: User) -> tuple[str, str]:
    """Generate and persist (encrypted) a new TOTP secret; return (secret, uri).

    The secret is returned once so the client can render a QR code. It is not
    active until :func:`enable_totp` succeeds.
    """
    secret = generate_totp_secret()
    user.totp_secret_encrypted = encrypt_secret(secret)
    user.totp_enabled = False
    await record_event(session, action=AuditAction.TWOFA_SETUP, actor_user_id=user.id)
    await session.flush()
    return secret, totp_provisioning_uri(secret, user.email)


async def enable_totp(session: AsyncSession, user: User, code: str) -> None:
    if not user.totp_secret_encrypted:
        raise TwoFactorNotInitialized
    secret = decrypt_secret(user.totp_secret_encrypted)
    if not verify_totp(secret, code):
        await record_event(session, action=AuditAction.TWOFA_FAILURE, actor_user_id=user.id)
        await session.flush()
        raise InvalidTwoFactorCode
    user.totp_enabled = True
    await record_event(session, action=AuditAction.TWOFA_ENABLED, actor_user_id=user.id)
    await session.flush()


async def disable_totp(session: AsyncSession, user: User, code: str) -> None:
    if not user.totp_secret_encrypted or not user.totp_enabled:
        raise TwoFactorNotInitialized
    secret = decrypt_secret(user.totp_secret_encrypted)
    if not verify_totp(secret, code):
        await record_event(session, action=AuditAction.TWOFA_FAILURE, actor_user_id=user.id)
        await session.flush()
        raise InvalidTwoFactorCode
    user.totp_enabled = False
    user.totp_secret_encrypted = None
    await record_event(session, action=AuditAction.TWOFA_DISABLED, actor_user_id=user.id)
    await session.flush()
