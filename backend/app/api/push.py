"""Push account settings + Telegram linking (Phase 11).

Web Push subscribe/unsubscribe and the Telegram deep-link flow. The Telegram
webhook is public (Telegram calls it) and is authenticated by a shared secret
echoed in ``X-Telegram-Bot-Api-Secret-Token``, compared in constant time; a
missing/wrong secret is logged and answered with ``200 OK`` (empty body) so
Telegram does not retry and spam the logs.
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_session
from app.core.deps import CurrentUser, get_db, get_settings_dep, require_push_tier
from app.models.live import PushChannel, PushSubscription
from app.models.user import User
from app.schemas.push import (
    SubscriptionOut,
    SubscriptionsOut,
    TelegramLinkOut,
    VapidKeyOut,
)
from app.services.push.telegram_link import consume_link_token, create_link_token

logger = logging.getLogger("push")

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidKeyOut)
async def vapid_public_key(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> VapidKeyOut:
    """Public — the browser needs this key to create a Web Push subscription."""
    return VapidKeyOut(public_key=settings.webpush_vapid_public_key)


@router.get("/subscriptions", response_model=SubscriptionsOut)
async def list_subscriptions(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SubscriptionsOut:
    subs = list(
        (
            await session.execute(
                select(PushSubscription).where(PushSubscription.user_id == user.id)
            )
        ).scalars()
    )
    return SubscriptionsOut(
        subscriptions=[SubscriptionOut(id=s.id, channel=s.channel) for s in subs],
        telegram_connected=any(s.channel == PushChannel.telegram for s in subs),
    )


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    subscription_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Remove one of the caller's push destinations (own-only)."""
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.id == subscription_id, PushSubscription.user_id == user.id
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_telegram(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Disconnect Telegram: drop the caller's Telegram subscription(s)."""
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user.id,
            PushSubscription.channel == PushChannel.telegram,
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/telegram/link", response_model=TelegramLinkOut)
async def telegram_link(
    user: Annotated[User, Depends(require_push_tier)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TelegramLinkOut:
    """Mint a one-time deep link the user opens to connect their Telegram chat."""
    token, expires_at = await create_link_token(session, user_id=user.id)
    await session.commit()
    url = f"https://t.me/{settings.telegram_bot_username}?start={token}"
    return TelegramLinkOut(url=url, expires_at=expires_at)


def _extract_start_token(update: dict[str, object]) -> tuple[str, str] | None:
    """Pull (chat_id, token) from a Telegram ``/start <token>`` update, or None."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    chat = message.get("chat")
    if not isinstance(text, str) or not isinstance(chat, dict):
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] != "/start":
        return None
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    return str(chat_id), parts[1]


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Telegram bot webhook. Always answers 200 (empty) so Telegram never retries;
    a bad/missing secret is logged, not surfaced."""
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = settings.telegram_webhook_secret
    # constant-time; also reject when no secret is configured (webhook disabled).
    if not expected or not hmac.compare_digest(provided, expected):
        logger.warning(json.dumps({"event": "telegram_webhook_rejected"}))
        return Response(status_code=status.HTTP_200_OK)

    try:
        update = await request.json()
    except (json.JSONDecodeError, ValueError):
        return Response(status_code=status.HTTP_200_OK)
    if not isinstance(update, dict):
        return Response(status_code=status.HTTP_200_OK)

    parsed = _extract_start_token(update)
    if parsed is not None:
        chat_id, token = parsed
        user_id = await consume_link_token(session, token)
        if user_id is not None:
            await session.execute(
                pg_insert(PushSubscription)
                .values(
                    user_id=user_id,
                    channel=PushChannel.telegram,
                    endpoint=chat_id,
                    keys={},
                )
                .on_conflict_do_nothing(constraint="uq_push_subscription")
            )
            await session.commit()
    return Response(status_code=status.HTTP_200_OK)
