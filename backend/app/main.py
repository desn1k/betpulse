"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.backtester import router as backtester_router
from app.api.health import router as health_router
from app.api.ingestion import router as ingestion_router
from app.api.live import router as live_router
from app.api.llm import admin_router as llm_admin_router
from app.api.llm import router as llm_router
from app.api.matches import router as matches_router
from app.api.models import router as models_router
from app.api.performance import router as performance_router
from app.api.promo import admin_router as promo_admin_router
from app.api.promo import router as promo_router
from app.api.providers import router as providers_router
from app.api.push import router as push_router
from app.api.system import audit_router
from app.api.system import router as system_router
from app.api.users import admin_router as users_admin_router
from app.core.config import get_settings
from app.core.deps import client_ip_from_forwarded
from app.core.redis import get_redis
from app.core.security_headers import SECURITY_HEADERS
from app.services.rate_limit import RateLimitExceeded, enforce_admin_mutation_ip_limit

_ADMIN_MUTATION_METHODS = frozenset({"DELETE", "PATCH", "POST", "PUT"})
_CORS_ALLOWED_METHODS = ["DELETE", "GET", "OPTIONS", "PATCH", "POST", "PUT"]
_CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type", "Last-Event-ID", "X-CSRF-Token"]


def _with_security_headers(response: Response) -> Response:
    for header, value in SECURITY_HEADERS.items():
        if header not in response.headers:
            response.headers[header] = value
    return response


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Football Analytics & ML Prediction Platform",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        debug=settings.debug,
    )

    @app.middleware("http")
    async def enforce_admin_mutation_rate_limit(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path.startswith("/admin") and request.method in _ADMIN_MUTATION_METHODS:
            try:
                await enforce_admin_mutation_ip_limit(
                    get_redis(),
                    ip=client_ip_from_forwarded(request),
                    limit=settings.rate_limit_admin_mutation_per_minute,
                )
            except RateLimitExceeded as exc:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Too many admin mutation attempts"},
                    headers={"Retry-After": str(exc.retry_after)},
                )
        return await call_next(request)

    # Registration order matters: CORS wraps the rate limiter so early 429
    # responses remain readable by allowed browser origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=_CORS_ALLOWED_METHODS,
        allow_headers=_CORS_ALLOWED_HEADERS,
        expose_headers=["Retry-After"],
    )

    @app.middleware("http")
    async def add_security_headers(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        return _with_security_headers(response)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(performance_router)
    app.include_router(live_router)
    app.include_router(matches_router)
    app.include_router(llm_router)
    app.include_router(llm_admin_router)
    app.include_router(promo_router)
    app.include_router(promo_admin_router)
    app.include_router(backtester_router)
    app.include_router(push_router)
    app.include_router(providers_router)
    app.include_router(ingestion_router)
    app.include_router(models_router)
    app.include_router(users_admin_router)
    app.include_router(system_router)
    app.include_router(audit_router)
    return app


app = create_app()
