"""Live provider resolution.

Phase 5 builds the API-Football provider from settings (dev/CI fallback key). In
production the key comes from an encrypted ``provider_accounts`` row set in the
Admin UI; wiring that lookup here is a drop-in replacement with no call-site
change.
"""

from __future__ import annotations

from app.core.config import Settings
from app.providers.api_football import ApiFootballProvider


def live_provider_configured(settings: Settings) -> bool:
    """Whether the live provider has a key. The one check the live loop makes
    (worker start-up and every poll): without a key no request is sent, so the
    provider never answers 401/403 every minute. The Admin → Providers key
    lookup, and a move of live to Sportmonks, replace it here."""
    return bool(settings.api_football_key.strip())


def build_live_provider(settings: Settings) -> ApiFootballProvider:
    return ApiFootballProvider(
        api_key=settings.api_football_key,
        base_url=settings.api_football_base_url,
    )
