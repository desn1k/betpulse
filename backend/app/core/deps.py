"""FastAPI dependencies: DB/Redis, current user, RBAC, verification, CSRF."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import rate_limit_bucket, resolve_client_ip
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import (
    TokenState,
    constant_time_equals,
    decode_access_token,
    token_state,
)
from app.models.user import User, UserRole
from app.services.tiers import ResolvedTier, resolve_tier_context

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


def get_settings_dep() -> Settings:
    return get_settings()


def get_redis_dep() -> Redis:
    return get_redis()


def get_client_ip(request: Request) -> str:
    """The caller's real IP: ``X-Forwarded-For`` is honoured only from a peer in
    ``TRUSTED_PROXY_CIDRS`` (see :mod:`app.core.client_ip`). Audit logs store
    this full address; rate limits key on :func:`rate_limit_bucket` of it."""
    peer = request.client.host if request.client is not None else None
    return resolve_client_ip(
        peer, request.headers.get("x-forwarded-for"), get_settings().trusted_proxy_networks
    )


def _session_revoked() -> HTTPException:
    """A genuine, unexpired access token issued before the account's credentials
    changed (ER2-01). The same answer on every route, public ones included, so the
    client renews the session (or signs out) on its next request; the header tells
    it apart from an ordinary 401. Raised from the auth dependency, before the
    route does any work, so the client may replay the request after renewing."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session revoked",
        headers={"WWW-Authenticate": "Bearer", "X-Session-Revoked": "true"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:  # noqa: TRY003
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:  # noqa: TRY003
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject"
        ) from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    # Revocation first: disabling an account also sets the timestamp, and the
    # revoked answer makes the client sign out instead of just failing.
    state = token_state(claims, user.credentials_changed_at)
    if state == TokenState.REVOKED:
        raise _session_revoked()
    if state == TokenState.INVALID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """Resolve the caller for a **public** endpoint, or ``None`` for a guest.

    No credentials → guest. A present-but-invalid/expired token is also treated
    as guest (best-effort) rather than 401, so a public page still renders for a
    client whose access token has lapsed; the client refreshes out of band.
    A genuine, unexpired token revoked by a credentials change is the exception:
    401 with ``X-Session-Revoked``, so the session ends (or renews) on the next
    request instead of silently turning into a guest (ER2-01).
    """
    if credentials is None:
        return None
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    user = await session.get(User, user_id)
    if user is None:
        return None
    # Revocation first (a disabled account's tokens are revoked too), then the
    # inactive-account guest fallback.
    state = token_state(claims, user.credentials_changed_at)
    if state == TokenState.REVOKED:
        raise _session_revoked()
    if state == TokenState.INVALID or not user.is_active:
        return None
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


class TierContext:
    """The caller's resolved tier plus the identity used for per-day limits."""

    def __init__(self, tier: ResolvedTier, user: User | None, identity: str) -> None:
        self.tier = tier
        self.user = user
        self.identity = identity


async def get_tier_context(
    request: Request,
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> TierContext:
    tier = await resolve_tier_context(session, redis, user)
    identity = str(user.id) if user is not None else rate_limit_bucket(get_client_ip(request))
    return TierContext(tier=tier, user=user, identity=identity)


TierContextDep = Annotated[TierContext, Depends(get_tier_context)]


async def require_push_tier(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> User:
    """Only tiers that may receive pushes (Pro/Expert, ``pushes_per_day != 0``)
    can subscribe, follow, or link Telegram. Guest is already blocked by auth;
    free is blocked here. Single source of truth: the ``pushes_per_day`` limit."""
    tier = await resolve_tier_context(session, redis, user)
    if not tier.can_receive_push():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "push_requires_upgrade", "tier_required": "pro"},
        )
    return user


def require_role(
    *roles: UserRole,
) -> Callable[[User], Awaitable[User]]:
    """Dependency factory enforcing that the current user has one of ``roles``."""

    async def _dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return user

    return _dependency


async def require_verified(
    user: CurrentUser, settings: Annotated[Settings, Depends(get_settings_dep)]
) -> User:
    """Require a verified email — only enforced when the feature flag is on."""
    if settings.email_verification_required and not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not verified")
    return user


async def require_admin(
    user: CurrentUser, settings: Annotated[Settings, Depends(get_settings_dep)]
) -> User:
    """Admin gate: role must be admin, the bootstrap password must have been
    changed, and 2FA must be enabled when the deployment requires it."""
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required before using admin features",
        )
    if settings.admin_2fa_required and not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Two-factor authentication must be enabled",
        )
    return user


async def verify_csrf(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Double-submit CSRF check for cookie-authenticated endpoints (refresh).

    The CSRF token is delivered both as a non-httpOnly cookie and in a request
    header; a forged cross-site request cannot read the cookie to echo it back.
    """
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    if not cookie_token or not csrf_header or not constant_time_equals(cookie_token, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
