"""Live provider resolution.

Phase 5 builds the API-Football provider from settings (dev/CI fallback key). In
production the key comes from an encrypted ``provider_accounts`` row set in the
Admin UI; wiring that lookup here is a drop-in replacement with no call-site
change.
"""

from __future__ import annotations

from app.core.config import Settings
from app.providers.api_football import ApiFootballProvider


def build_live_provider(settings: Settings) -> ApiFootballProvider:
    return ApiFootballProvider(
        api_key=settings.api_football_key,
        base_url=settings.api_football_base_url,
    )
