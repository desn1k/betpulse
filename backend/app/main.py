"""FastAPI application entrypoint.

Phase 1 exposes only health probes so the container, CI, and the Docker
Compose stack have something concrete to check. Feature routers are mounted in
later phases.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.health import router as health_router
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

    app.include_router(health_router)
    return app


app = create_app()
