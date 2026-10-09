"""Authentication routes.

Access tokens are returned in the JSON body (client keeps them in memory).
Refresh tokens are delivered only as an httpOnly + SameSite=Strict cookie scoped
to ``/auth/refresh``; a companion non-httpOnly CSRF cookie enables the
double-submit check on refresh/logout.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.deps import (
    CurrentUser,
    get_client_ip,
    get_db,
    get_redis_dep,
    verify_csrf,
)
from app.schemas.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    TwoFACodeRequest,
    TwoFASetupResponse,
    UserOut,
    VerifyEmailRequest,
)
from app.services import auth as auth_service
from app.services import twofa as twofa_service
from app.services.auth import IssuedTokens
from app.services.rate_limit import (
    RateLimitExceeded,
    enforce_login_ip_limit,
    enforce_user_limit,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Per-user limits on account-security actions (ER2-02) ---------------------
# Route dependencies, so they run before the handler body: the password change
# is counted before Argon2, a TOTP code before it is checked, a setup before a
# new secret is generated.


async def _enforce_account_limit(
    redis: Redis, *, scope: str, user_id: str, limit: int, window_seconds: int
) -> None:
    try:
        await enforce_user_limit(
            redis, scope=scope, user_id=user_id, limit=limit, window_seconds=window_seconds
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


async def limit_password_change(
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis_dep)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    await _enforce_account_limit(
        redis,
        scope="password_change",
        user_id=str(user.id),
        limit=settings.rate_limit_password_change_attempts,
        window_seconds=settings.rate_limit_account_security_window_seconds,
    )


async def limit_totp_code(
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis_dep)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    await _enforce_account_limit(
        redis,
        scope="totp_code",
        user_id=str(user.id),
        limit=settings.rate_limit_totp_code_attempts,
        window_seconds=settings.rate_limit_account_security_window_seconds,
    )


async def limit_totp_setup(
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis_dep)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    await _enforce_account_limit(
        redis,
        scope="totp_setup",
        user_id=str(user.id),
        limit=settings.rate_limit_totp_setup_per_hour,
        window_seconds=3600,
    )


def _set_auth_cookies(response: Response, tokens: IssuedTokens, settings: Settings) -> None:
    max_age = settings.jwt_refresh_ttl_days * 24 * 3600
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=tokens.refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path=settings.refresh_cookie_path,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=tokens.csrf_token,
        max_age=max_age,
        httponly=False,  # readable by the client to echo back in a header
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    # Same name/path (and attributes) as when set, or the browser keeps them.
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
    )


def _refresh_rejected(detail: str, settings: Settings) -> JSONResponse:
    """A 401 that also clears the auth cookies. Returned, not raised: headers set
    on the injected ``Response`` are dropped when an ``HTTPException`` is raised,
    so the cookies would otherwise survive."""
    response = JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": detail})
    _clear_auth_cookies(response, settings)
    return response


def _token_response(tokens: IssuedTokens) -> AccessTokenResponse:
    return AccessTokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=UserOut.model_validate(tokens.user),
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user_agent: Annotated[str | None, Header()] = None,
) -> UserOut:
    try:
        user = await auth_service.register_user(
            session,
            email=payload.email,
            password=payload.password,
            ip=get_client_ip(request),
            user_agent=user_agent,
        )
    except auth_service.EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    # A verification token is created now; email delivery is wired in a later phase.
    await auth_service.create_email_verification_token(session, user)
    return UserOut.model_validate(user)


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_agent: Annotated[str | None, Header()] = None,
) -> AccessTokenResponse:
    ip = get_client_ip(request)
    try:
        await enforce_login_ip_limit(redis, ip=ip, limit=settings.rate_limit_login_per_minute)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    try:
        user = await auth_service.authenticate(
            session,
            email=payload.email,
            password=payload.password,
            totp_code=payload.totp_code,
            ip=ip,
            user_agent=user_agent,
        )
    except auth_service.AccountLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except auth_service.TwoFactorRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-factor code required",
            headers={"X-2FA-Required": "true"},
        ) from exc
    except (auth_service.InvalidCredentials, auth_service.TwoFactorInvalid) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        ) from exc

    tokens = await auth_service.issue_token_pair(session, user)
    _set_auth_cookies(response, tokens, settings)
    return _token_response(tokens)


@router.post("/refresh", response_model=AccessTokenResponse, dependencies=[Depends(verify_csrf)])
async def refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    user_agent: Annotated[str | None, Header()] = None,
) -> AccessTokenResponse | JSONResponse:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        return _refresh_rejected("Missing refresh token", settings)
    # Rotation commits its own transaction (see rotate_refresh_token), so a
    # revocation it records survives the 401 below.
    try:
        tokens = await auth_service.rotate_refresh_token(
            refresh_token=refresh_token,
            ip=get_client_ip(request),
            user_agent=user_agent,
            redis=redis,
        )
    except auth_service.RefreshConflict as exc:
        # A concurrent request just rotated this token. Leave the cookies alone:
        # clearing them here could wipe the ones the winning response set.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Refresh already in progress",
            headers={"Retry-After": "1"},
        ) from exc
    except (auth_service.InvalidToken, auth_service.TokenReuseDetected):
        return _refresh_rejected("Invalid refresh token", settings)

    _set_auth_cookies(response, tokens, settings)
    return _token_response(tokens)


@router.post("/logout", response_model=MessageResponse, dependencies=[Depends(verify_csrf)])
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_agent: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if refresh_token:
        await auth_service.logout(
            session,
            refresh_token=refresh_token,
            ip=get_client_ip(request),
            user_agent=user_agent,
        )
    _clear_auth_cookies(response, settings)
    return MessageResponse(detail="Logged out")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    dependencies=[Depends(limit_password_change)],
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    user_agent: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    try:
        await auth_service.change_password(
            session,
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            ip=get_client_ip(request),
            user_agent=user_agent,
        )
    except auth_service.InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        ) from exc
    return MessageResponse(detail="Password changed")


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    user_agent: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    try:
        await auth_service.verify_email(
            session, token=payload.token, ip=get_client_ip(request), user_agent=user_agent
        )
    except auth_service.InvalidToken as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token"
        ) from exc
    return MessageResponse(detail="Email verified")


@router.post(
    "/2fa/setup",
    response_model=TwoFASetupResponse,
    dependencies=[Depends(limit_totp_setup)],
)
async def twofa_setup(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TwoFASetupResponse:
    try:
        secret, uri = await twofa_service.setup_totp(session, user)
    except twofa_service.TwoFactorAlreadyEnabled as exc:
        # F11: an enabled second factor is rotated only through disable + code.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="two_factor_already_enabled"
        ) from exc
    return TwoFASetupResponse(secret=secret, provisioning_uri=uri)


@router.post(
    "/2fa/enable",
    response_model=MessageResponse,
    dependencies=[Depends(limit_totp_code)],
)
async def twofa_enable(
    payload: TwoFACodeRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    try:
        await twofa_service.enable_totp(session, user, payload.code)
    except twofa_service.TwoFactorNotInitialized as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Run 2FA setup first"
        ) from exc
    except twofa_service.InvalidTwoFactorCode as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code") from exc
    return MessageResponse(detail="Two-factor authentication enabled")


@router.post(
    "/2fa/disable",
    response_model=MessageResponse,
    dependencies=[Depends(limit_totp_code)],
)
async def twofa_disable(
    payload: TwoFACodeRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    try:
        await twofa_service.disable_totp(session, user, payload.code)
    except twofa_service.TwoFactorNotInitialized as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="2FA is not enabled"
        ) from exc
    except twofa_service.InvalidTwoFactorCode as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code") from exc
    return MessageResponse(detail="Two-factor authentication disabled")
