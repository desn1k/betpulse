"""Audit-log writer.

Records both successful and failed security events. Callers pass an
:class:`AsyncSession`; the row is flushed within the caller's transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditAction:
    """Canonical audit action names."""

    REGISTER = "auth.register"
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILURE = "auth.login.failure"
    LOGIN_LOCKED = "auth.login.locked"
    LOGOUT = "auth.logout"
    TOKEN_REFRESH = "auth.token.refresh"  # nosec B105  (action name, not a secret)
    TOKEN_REUSE_DETECTED = "auth.token.reuse_detected"  # nosec B105
    TWOFA_SETUP = "auth.2fa.setup"
    TWOFA_ENABLED = "auth.2fa.enabled"
    TWOFA_DISABLED = "auth.2fa.disabled"
    TWOFA_FAILURE = "auth.2fa.failure"
    PASSWORD_CHANGED = "auth.password.changed"  # nosec B105  (action name)
    EMAIL_VERIFIED = "auth.email.verified"


async def record_event(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    target: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target=target,
            ip=ip,
            user_agent=user_agent,
            meta=meta or {},
        )
    )
    await session.flush()
