"""Ops alert delivery for admin-triggered system notifications (Phase 12d)."""

from __future__ import annotations

import httpx

from app.core.config import Settings


class OpsAlertNotConfigured(Exception):
    """Raised when Telegram ops alerts have not been configured."""


class OpsAlertDeliveryFailed(Exception):
    """Raised when the Telegram API rejects an ops alert."""


async def send_ops_alert(settings: Settings, message: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        raise OpsAlertNotConfigured
    url = f"{settings.telegram_api_base_url}/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            json={"chat_id": settings.telegram_alert_chat_id, "text": message},
        )
    if resp.status_code >= 400:
        raise OpsAlertDeliveryFailed(f"telegram send failed: {resp.status_code}")
