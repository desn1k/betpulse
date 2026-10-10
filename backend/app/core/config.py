"""Application configuration loaded from the environment.

Values mirror the keys documented in ``.env.example``. Secrets never carry a
real default; instead a model validator refuses to boot in production when a
security-critical key is missing or weak.
"""

from __future__ import annotations

import ipaddress
import math
from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.client_ip import (
    IPNetwork,
    parse_networks,
    parse_trusted_proxies,
    validate_production_proxies,
)

# --- placeholder / weak secret detection ---------------------------------------------
# Docker Compose reads an env_file line `KEY=   # comment` as the value
# `# comment`, so a documented hint can silently become a secret. Production
# refuses such values; .env.example keeps its comments on their own lines.

# Values that appear as examples in docs or are common stand-ins.
_PLACEHOLDER_VALUES = frozenset(
    {
        "change-me",
        "changeme",
        "change_me",
        "password",
        "passw0rd",
        "secret",
        "placeholder",
        "todo",
        "test",
        "admin",
        "football",
        "minioadmin",
    }
)
SECRET_MIN_BITS = 128
ADMIN_PASSWORD_MIN_LENGTH = 12  # same floor as PasswordStr in app/schemas/auth.py
ADMIN_PASSWORD_MIN_BITS = 40


def entropy_bits(value: str) -> float:
    """Shannon estimate of the value's information content (length x bits per
    character of its own character distribution): a cheap guard against
    repeated or patterned stand-ins, not a strength meter."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    per_char = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return per_char * n


# A long value from a CSPRNG uses many distinct characters: token_hex(32) has
# 16 distinct hex digits almost surely, token_urlsafe(32) ~30. Fewer than this
# many in a value of LONG_VALUE_LENGTH+ characters means it was typed, not drawn.
LONG_VALUE_LENGTH = 32
LONG_VALUE_MIN_DISTINCT = 10
# abcdefgh / 01234567 / hgfedcba: a keyboard walk, not randomness. For
# token_hex(32) the chance of such a run is ~4e-7 per key.
MONOTONIC_RUN_LENGTH = 8


def _is_periodic(value: str) -> bool:
    """True when the value is a shorter block repeated (abab..., 0123...0123)."""
    return len(value) > 1 and value in (value + value)[1:-1]


def _has_monotonic_run(value: str, length: int = MONOTONIC_RUN_LENGTH) -> bool:
    """True when `length` consecutive characters step by +1 (or by -1) each."""
    up = down = 1
    for prev, cur in zip(value, value[1:], strict=False):
        step = ord(cur) - ord(prev)
        up = up + 1 if step == 1 else 1
        down = down + 1 if step == -1 else 1
        if up >= length or down >= length:
            return True
    return False


def is_placeholder_secret(value: str) -> bool:
    """True for an empty value, a comment that leaked into a value (`#...`), a
    documented example (anything containing "example") or common stand-in, or a
    value that is visibly not random: almost no character variety, a repeated
    block, too few distinct characters for its length, or a long run such as
    abcdefgh / 01234567. Shannon entropy alone misses the last three."""
    v = value.strip()
    if not v or v.startswith("#"):
        return True
    low = v.lower()
    if low in _PLACEHOLDER_VALUES or "example" in low:
        return True
    if len(set(v)) <= 3 or _is_periodic(low) or _has_monotonic_run(low):
        return True
    return len(v) >= LONG_VALUE_LENGTH and len(set(v)) < LONG_VALUE_MIN_DISTINCT


def secret_problem(value: str) -> str | None:
    """Why ``value`` cannot sign tokens in production, or None."""
    if is_placeholder_secret(value):
        return "is empty or a placeholder"
    if len(value) < 32:
        return "is shorter than 32 characters"
    if entropy_bits(value) < SECRET_MIN_BITS:
        return f"has too little entropy (< {SECRET_MIN_BITS} bits)"
    return None


def encryption_key_problem(value: str) -> str | None:
    """Why ``value`` is not a usable DATA_ENCRYPTION_KEY (64 hex chars), or None."""
    if is_placeholder_secret(value):
        return "is empty or a placeholder"
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return "is not hex (generate it with `openssl rand -hex 32`)"
    if len(raw) != 32:
        return "must decode to 32 bytes (64 hex characters)"
    if entropy_bits(value) < SECRET_MIN_BITS:
        return f"has too little entropy (< {SECRET_MIN_BITS} bits)"
    return None


def admin_password_problem(value: str) -> str | None:
    """Why ``value`` cannot be the first admin's password, or None."""
    if is_placeholder_secret(value):
        return "is empty, a placeholder or a documented example"
    if len(value) < ADMIN_PASSWORD_MIN_LENGTH:
        return f"is shorter than {ADMIN_PASSWORD_MIN_LENGTH} characters"
    if entropy_bits(value) < ADMIN_PASSWORD_MIN_BITS:
        return "is too predictable"
    return None


Environment = Literal["development", "staging", "production"]


class ReferenceBookmakerRange(BaseModel):
    """Which bookmaker's closing odds are the reference for matches kicking off
    in ``[from_date, until_date)`` (UTC dates; ``None`` = open-ended)."""

    bookmaker: str
    from_date: date | None = None
    until_date: date | None = None


# Pinnacle's feed became unreliable on 2025-07-23 (football-data.co.uk notes);
# from then on the market-average closing price is the reference.
DEFAULT_REFERENCE_BOOKMAKERS = [
    ReferenceBookmakerRange(bookmaker="pinnacle", until_date=date(2025, 7, 23)),
    ReferenceBookmakerRange(bookmaker="market_avg", from_date=date(2025, 7, 23)),
]

# Local runs (uvicorn and Next.js on one host) and the test client.
_DEV_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"


def _validate_internal_networks(
    internal: tuple[IPNetwork, ...], trusted: tuple[IPNetwork, ...]
) -> None:
    """Production: our own network is listed, private, and holds every trusted
    proxy (they are its pinned addresses), so the two settings cannot drift."""
    if not internal:
        raise ValueError(
            "INTERNAL_NETWORK_CIDRS must be set in production (the Compose network's subnets)"
        )
    for network in internal:
        if not network.is_private:
            raise ValueError(f"INTERNAL_NETWORK_CIDRS: {network} is not a private network")
    for proxy in trusted:
        if not any(_within(proxy, network) for network in internal):
            raise ValueError(f"TRUSTED_PROXY_CIDRS: {proxy} is outside INTERNAL_NETWORK_CIDRS")


def _within(inner: IPNetwork, outer: IPNetwork) -> bool:
    if isinstance(inner, ipaddress.IPv4Network) and isinstance(outer, ipaddress.IPv4Network):
        return inner.subnet_of(outer)
    if isinstance(inner, ipaddress.IPv6Network) and isinstance(outer, ipaddress.IPv6Network):
        return inner.subnet_of(outer)
    return False


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
        # A validation error must never echo the settings it was given: they
        # hold every secret (pydantic otherwise prints `input_value=...`).
        hide_input_in_errors=True,
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
    # A refresh token presented again within this many seconds of its rotation
    # (double-click, two tabs) gets 409 instead of tripping family revocation.
    refresh_reuse_grace_seconds: int = 10
    # F13 B: a rotated token presented again within this many seconds, from the
    # rotating request's user-agent and subnet while its successor is unused,
    # gets that same successor back (at most REFRESH_REPLAY_MAX_USES times).
    # 0 turns it off; otherwise between the grace window above and 300.
    refresh_replay_window_seconds: int = 60
    refresh_replay_max_uses: int = 3

    # --- Rate limiting / lockout -------------------------------------------
    rate_limit_login_per_minute: int = 5  # per client IP
    login_max_failures: int = 5  # per account before backoff kicks in
    lockout_base_seconds: int = 30  # exponential backoff base
    lockout_max_seconds: int = 3600
    rate_limit_promo_per_hour: int = 10
    rate_limit_llm_analysis_per_minute: int = 20
    rate_limit_admin_mutation_per_minute: int = 60
    # Successful refresh-token replays (F13 B) per client IP (IPv6 per /64); over
    # it a replay falls through to the ordinary rotation rules, never a 429.
    rate_limit_refresh_replay_per_minute: int = 10
    # GET /matches/{id}, per caller (user id, or guest IP / IPv6 /64), checked
    # before the fixture lookup. A match page refetches once a minute per tab,
    # and guests behind one NAT share a bucket, hence the headroom.
    rate_limit_match_detail_per_window: int = 120
    rate_limit_match_detail_window_seconds: int = 60
    # GET /matches (the list), per caller like the detail limit (F7). One page
    # view is 1 request, each new filter 1, the poll 1 a minute per tab; the
    # headroom is for guests sharing an address (office NAT, carrier NAT).
    rate_limit_match_list_per_window: int = 120
    rate_limit_match_list_window_seconds: int = 60
    # POST /auth/refresh per client IP (IPv6 per /64), every attempt counted,
    # before the rotation (F7). A user makes a few a minute (page loads, the
    # renewal timer, tab focus); the headroom is for users behind one NAT.
    rate_limit_refresh_per_minute: int = 120
    # Account security, per user, counted before the expensive or guessable
    # check (ER2-02): password change before Argon2, TOTP enable/disable before
    # the 6-digit code (one shared bucket), TOTP setup before a new secret.
    rate_limit_password_change_attempts: int = 5
    rate_limit_totp_code_attempts: int = 5
    rate_limit_account_security_window_seconds: int = 900
    rate_limit_totp_setup_per_hour: int = 5

    # --- Reverse proxies ----------------------------------------------------
    # Comma-separated CIDRs whose X-Forwarded-For is honoured (Caddy and the
    # Next.js BFF). Required in production; development/tests default to
    # loopback. See app/core/client_ip.py.
    trusted_proxy_cidrs: str | None = None
    # Comma-separated CIDRs of our own Docker network (both address families).
    # A client identity inside them is never a real client: it is the bridge
    # gateway (Docker's userland proxy relayed the connection, F5) or an
    # internal hop the API does not trust. The API logs and reports such
    # requests (app/services/client_identity.py). Required in production.
    internal_network_cidrs: str | None = None

    # --- Feature flags ------------------------------------------------------
    email_verification_required: bool = False
    admin_2fa_required: bool = True

    # --- Bootstrap admin (consumed by app.bootstrap, not the running app) ---
    admin_email: str = "admin@example.com"
    admin_password: str = ""

    # --- PostgreSQL ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://football:football@localhost:5432/football"
    database_read_url: str = ""  # optional read replica; empty → use primary
    # Per-process connection pools (see HANDOFF "Database connection budget").
    # Request sessions use the main pool; security-state transactions
    # (app.core.db.independent_transaction) use a separate small pool, so a
    # request holding a main-pool connection can never wait on its own pool.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_security_pool_size: int = 2
    db_security_max_overflow: int = 3

    # --- Redis --------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = ""

    # --- ML / MLflow / model governance -------------------------------------
    mlflow_tracking_uri: str = "http://localhost:5000"
    accuracy_window_days: int = 90
    # Reference bookmaker per kickoff date range (JSON list in the env, e.g.
    # [{"bookmaker":"pinnacle","until_date":"2025-07-23"},
    #  {"bookmaker":"market_avg","from_date":"2025-07-23"}]); ``until_date`` is
    # exclusive, ``from_date`` inclusive. Ranges must be contiguous, ordered, and
    # cover every date (first open at the start, last open at the end) — see
    # app.ml.odds_selection.reference_bookmaker_for.
    reference_bookmakers: list[ReferenceBookmakerRange] = DEFAULT_REFERENCE_BOOKMAKERS
    # A historical-ingestion run still "running" after this long belongs to a
    # dead worker (2x the batch queue's 30-minute job timeout) and is marked
    # failed; a younger one may still be alive and is left alone.
    ingestion_stale_run_minutes: int = 60
    consensus_weight_mode: Literal["auto", "manual"] = "auto"
    champion_min_samples: int = 300
    # A challenger replaces the champion only if its Brier (on the same fixtures)
    # is at least this much lower. Below the noise level at ~300 matches; a
    # paired-bootstrap significance test is a documented follow-up.
    champion_min_brier_improvement: float = 0.002
    # Max expected champion-reeval runtime; the Redis lock TTL is 2x this so a
    # crashed worker never holds the lock forever (see reevaluate_champions_task).
    champion_reeval_max_runtime_seconds: int = 600
    # How long an admin registry action waits for the registry lock (held by a
    # running re-evaluation or another admin action) before failing with 409
    # registry_busy instead of hanging the request (app.ml.registry_lock).
    registry_lock_timeout_ms: int = 5000

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
    # Push services a Web Push endpoint may point at, comma-separated: an exact
    # host, or a suffix starting with "." (any subdomain). Checked when a
    # subscription is stored and again before every send (SSRF guard,
    # app/services/live/push_endpoint.py).
    webpush_allowed_hosts: str = (
        "fcm.googleapis.com,updates.push.services.mozilla.com,"
        "web.push.apple.com,.notify.windows.com"
    )
    # At most one push per fixture per this many seconds (spec: 5 minutes).
    push_rate_limit_seconds: int = 300
    # On a delivery failure, retry exactly once after this delay, then discard.
    push_retry_delay_seconds: int = 30

    @property
    def vapid_subject(self) -> str:
        return f"mailto:{self.webpush_contact_email}"

    @property
    def webpush_allowed_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.webpush_allowed_hosts.split(",") if h.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def trusted_proxy_networks(self) -> tuple[IPNetwork, ...]:
        if self.trusted_proxy_cidrs is None:
            return () if self.is_production else parse_trusted_proxies(_DEV_TRUSTED_PROXIES)
        return parse_trusted_proxies(self.trusted_proxy_cidrs)

    @property
    def internal_networks(self) -> tuple[IPNetwork, ...]:
        if self.internal_network_cidrs is None:
            return ()
        return parse_networks(self.internal_network_cidrs, "INTERNAL_NETWORK_CIDRS")

    @property
    def read_database_url(self) -> str:
        """Read-replica URL, falling back to the primary when unset."""
        return self.database_read_url or self.database_url

    @field_validator(
        "rate_limit_match_list_per_window",
        "rate_limit_match_list_window_seconds",
        "rate_limit_refresh_per_minute",
    )
    @classmethod
    def _validate_positive_limit(cls, value: int, info: ValidationInfo) -> int:
        if value < 1:
            raise ValueError(f"{(info.field_name or '').upper()} must be at least 1")
        return value

    @field_validator("reference_bookmakers")
    @classmethod
    def _validate_reference_bookmakers(
        cls, ranges: list[ReferenceBookmakerRange]
    ) -> list[ReferenceBookmakerRange]:
        if not ranges:
            raise ValueError("REFERENCE_BOOKMAKERS needs at least one range")
        if ranges[0].from_date is not None or ranges[-1].until_date is not None:
            raise ValueError(
                "REFERENCE_BOOKMAKERS must cover all dates: the first range has no "
                "from_date and the last no until_date"
            )
        for earlier, later in zip(ranges, ranges[1:], strict=False):
            if earlier.until_date is None or earlier.until_date != later.from_date:
                raise ValueError(
                    "REFERENCE_BOOKMAKERS ranges must be ordered and contiguous "
                    "(each until_date equals the next from_date): no gaps, no overlaps"
                )
        for r in ranges:
            if r.from_date and r.until_date and r.from_date >= r.until_date:
                raise ValueError(f"empty REFERENCE_BOOKMAKERS range for {r.bookmaker}")
        return ranges

    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """Fail fast when production security settings are unsafe."""
        networks = self.trusted_proxy_networks  # raises on malformed CIDRs
        internal = self.internal_networks  # likewise
        window = self.refresh_replay_window_seconds
        if window != 0 and not self.refresh_reuse_grace_seconds <= window <= 300:
            raise ValueError(
                "REFRESH_REPLAY_WINDOW_SECONDS must be 0 (off) or between "
                "REFRESH_REUSE_GRACE_SECONDS and 300"
            )
        if self.refresh_replay_max_uses < 1:
            raise ValueError("REFRESH_REPLAY_MAX_USES must be at least 1")
        if self.is_production:
            if self.trusted_proxy_cidrs is None:
                raise ValueError("TRUSTED_PROXY_CIDRS must be set explicitly in production")
            validate_production_proxies(networks)
            _validate_internal_networks(internal, networks)
            problem = secret_problem(self.secret_key)
            if problem:
                raise ValueError(f"SECRET_KEY {problem}; set a random value in production")
            problem = encryption_key_problem(self.data_encryption_key)
            if problem:
                raise ValueError(f"DATA_ENCRYPTION_KEY {problem}")
            # Optional at startup (only `create-admin` reads it), never a placeholder.
            if self.admin_password:
                problem = admin_password_problem(self.admin_password)
                if problem:
                    raise ValueError(f"ADMIN_PASSWORD {problem}")
            if "*" in self.cors_origins:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must list explicit origins when credentials are enabled"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
