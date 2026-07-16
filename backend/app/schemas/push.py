"""Schemas for push subscription management + Telegram linking (Phase 11)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.live import PushChannel


class VapidKeyOut(BaseModel):
    """The server's VAPID public key, needed by the browser to subscribe."""

    public_key: str


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    channel: PushChannel


class SubscriptionsOut(BaseModel):
    subscriptions: list[SubscriptionOut]
    telegram_connected: bool


class FollowOut(BaseModel):
    fixture_id: uuid.UUID
    following: bool


class FollowsOut(BaseModel):
    fixture_ids: list[uuid.UUID]


class LatestSwingOut(BaseModel):
    """Public snapshot the service worker fetches to render a push notification."""

    fixture_id: uuid.UUID
    home_team: str
    away_team: str
    minute: int
    home_score: int
    away_score: int
    probs: dict[str, dict[str, float]]


class TelegramLinkOut(BaseModel):
    """Deep link the user opens to connect Telegram, plus its expiry."""

    url: str
    expires_at: datetime
