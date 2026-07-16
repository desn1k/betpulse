"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.backtester import router as backtester_router
from app.api.health import router as health_router
from app.api.live import router as live_router
from app.api.llm import admin_router as llm_admin_router
from app.api.llm import router as llm_router
from app.api.matches import router as matches_router
from app.api.performance import router as performance_router
from app.api.promo import admin_router as promo_admin_router
from app.api.promo import router as promo_router
from app.core.config import get_settings


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    return app


app = create_app()
