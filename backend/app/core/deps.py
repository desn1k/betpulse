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

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import constant_time_equals, decode_access_token
from app.models.user import User, UserRole

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


def get_settings_dep() -> Settings:
    return get_settings()


def get_redis_dep() -> Redis:
    return get_redis()


def get_client_ip(request: Request) -> str:
    """Best-effort client IP (trusts the direct peer; proxy headers are handled
    at the reverse proxy in production)."""
    if request.client is None:
        return "unknown"
    return request.client.host


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
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


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
