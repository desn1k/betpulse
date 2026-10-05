"""Request/response schemas for the live + push endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.live import PushChannel
from app.schemas.base import RequestModel


class PushSubscribeIn(RequestModel):
    channel: PushChannel
    # Telegram: the chat id. Web Push: the endpoint URL.
    endpoint: str = Field(min_length=1, max_length=512)
    # Web Push only: {"p256dh": ..., "auth": ...}. Empty for Telegram.
    keys: dict[str, str] = Field(default_factory=dict)


class PushSubscribeOut(BaseModel):
    id: uuid.UUID
    channel: PushChannel
