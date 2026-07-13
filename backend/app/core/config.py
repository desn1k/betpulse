"""Application configuration loaded from the environment.

Values mirror the keys documented in ``.env.example``. Only the settings that
Phase 1 actually needs are typed here; later phases extend this model as the
corresponding features land. Nothing secret is ever given a real default.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Typed view over the process environment.

    Unknown environment variables are ignored so that the full ``.env`` file
    (which carries keys for features built in later phases) does not break the
    app during early phases.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    debug: bool = False
    app_name: str = "football-analytics"
    default_locale: Literal["ru", "en"] = "ru"

    public_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    # Present so config validation exercises the security-critical keys even in
    # Phase 1; they are consumed by later phases.
    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        """CORS origins as a clean list."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
