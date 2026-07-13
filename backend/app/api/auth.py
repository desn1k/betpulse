"""Authentication routes.

Access tokens are returned in the JSON body (client keeps them in memory).
Refresh tokens are delivered only as an httpOnly + SameSite=Strict cookie scoped
to ``/auth/refresh``; a companion non-httpOnly CSRF cookie enables the
double-submit check on refresh/logout.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
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
from app.services.rate_limit import RateLimitExceeded, enforce_login_ip_limit

router = APIRouter(prefix="/auth", tags=["auth"])


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
    response.delete_cookie(settings.refresh_cookie_name, path=settings.refresh_cookie_path)
    response.delete_cookie(settings.csrf_cookie_name, path="/")


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
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_agent: Annotated[str | None, Header()] = None,
) -> AccessTokenResponse:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )
    try:
        tokens = await auth_service.rotate_refresh_token(
            session,
            refresh_token=refresh_token,
            ip=get_client_ip(request),
            user_agent=user_agent,
        )
    except (auth_service.InvalidToken, auth_service.TokenReuseDetected) as exc:
        _clear_auth_cookies(response, settings)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from exc

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


@router.post("/change-password", response_model=MessageResponse)
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


@router.post("/2fa/setup", response_model=TwoFASetupResponse)
async def twofa_setup(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TwoFASetupResponse:
    secret, uri = await twofa_service.setup_totp(session, user)
    return TwoFASetupResponse(secret=secret, provisioning_uri=uri)


@router.post("/2fa/enable", response_model=MessageResponse)
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


@router.post("/2fa/disable", response_model=MessageResponse)
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
