"""Application configuration loaded from the environment.

Values mirror the keys documented in ``.env.example``. Secrets never carry a
real default; instead a model validator refuses to boot in production when a
security-critical key is missing or weak.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
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

    # --- Core ---------------------------------------------------------------
    environment: Environment = "development"
    debug: bool = False
    app_name: str = "football-analytics"
    default_locale: Literal["ru", "en"] = "ru"

    public_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:3000"

    # --- Security / crypto --------------------------------------------------
    # 64-char hex (openssl rand -hex 32). Used to sign JWTs.
    secret_key: str = ""
    # 64-char hex; envelope key that encrypts stored secrets (TOTP, later
    # provider/LLM keys) at rest.
    data_encryption_key: str = ""

    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30

    # Argon2id parameters — see app/core/security.py for the rationale.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536  # 64 MiB
    argon2_parallelism: int = 4

    # --- Auth cookies / CSRF ------------------------------------------------
    auth_cookie_secure: bool = True  # override to false for local http dev
    refresh_cookie_name: str = "bp_refresh"
    refresh_cookie_path: str = "/auth/refresh"
    csrf_cookie_name: str = "bp_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    # --- Rate limiting / lockout -------------------------------------------
    rate_limit_login_per_minute: int = 5  # per client IP
    login_max_failures: int = 5  # per account before backoff kicks in
    lockout_base_seconds: int = 30  # exponential backoff base
    lockout_max_seconds: int = 3600
    rate_limit_promo_per_hour: int = 10

    # --- Feature flags ------------------------------------------------------
    email_verification_required: bool = False
    admin_2fa_required: bool = True

    # --- Bootstrap admin (consumed by app.bootstrap, not the running app) ---
    admin_email: str = "admin@example.com"
    admin_password: str = ""

    # --- PostgreSQL ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://football:football@localhost:5432/football"
    database_read_url: str = ""  # optional read replica; empty → use primary

    # --- Redis --------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""

    # --- ML / MLflow / model governance -------------------------------------
    mlflow_tracking_uri: str = "http://localhost:5000"
    accuracy_window_days: int = 90
    consensus_weight_mode: Literal["auto", "manual"] = "auto"
    champion_min_samples: int = 300
    # Max expected champion-reeval runtime; the Redis lock TTL is 2x this so a
    # crashed worker never holds the lock forever (see reevaluate_champions_task).
    champion_reeval_max_runtime_seconds: int = 600

    # --- Live provider (API-Football) --------------------------------------
    # Dev/CI fallback only; production keys are entered in Admin → Providers and
    # stored encrypted at rest (never read from the environment in prod).
    api_football_key: str = ""
    api_football_base_url: str = "https://v3.football.api-sports.io"

    # --- Live ingestion / in-play recompute --------------------------------
    live_poll_interval_seconds: int = 60
    # A live recompute enqueues a push job only when a probability moves by more
    # than this (absolute) versus the previous in-play row.
    probability_swing_push_threshold: float = 0.10
    # SSE reconnect replay window: on `Last-Event-ID` we replay at most this many
    # seconds of buffered live updates (spec: no more than 5 minutes).
    live_replay_window_seconds: int = 300
    # Redis pub/sub channel that fans out live updates between API replicas.
    live_events_channel: str = "live:events"

    # --- Push notifications -------------------------------------------------
    telegram_bot_token: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"
    # Bot @username used to build the deep link t.me/<username>?start=<token>.
    telegram_bot_username: str = ""
    # Chat id for admin/system ops alerts (backup/CI/ingest/system checks).
    telegram_alert_chat_id: str = ""
    # Shared secret Telegram echoes in ``X-Telegram-Bot-Api-Secret-Token`` on
    # every webhook call; verified in constant time. Empty = webhook disabled.
    telegram_webhook_secret: str = ""
    # Web Push (VAPID). Keys are base64url-encoded EC P-256 (prime256v1); the
    # contact becomes the ``mailto:`` subject required by the Web Push protocol.
    webpush_vapid_private_key: str = ""
    webpush_vapid_public_key: str = ""
    webpush_contact_email: str = "admin@example.com"
    # At most one push per fixture per this many seconds (spec: 5 minutes).
    push_rate_limit_seconds: int = 300
    # On a delivery failure, retry exactly once after this delay, then discard.
    push_retry_delay_seconds: int = 30

    @property
    def vapid_subject(self) -> str:
        return f"mailto:{self.webpush_contact_email}"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def read_database_url(self) -> str:
        """Read-replica URL, falling back to the primary when unset."""
        return self.database_read_url or self.database_url

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        """Fail fast in production if security-critical keys are missing."""
        if self.is_production:
            for name in ("secret_key", "data_encryption_key"):
                value = getattr(self, name)
                if not value or len(value) < 32:
                    raise ValueError(f"{name.upper()} must be set to a strong value in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
