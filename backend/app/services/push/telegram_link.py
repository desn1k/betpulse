"""Telegram account linking via a one-time deep-link token (Phase 11).

Flow: the user (Pro/Expert) requests a link → we mint a random token, store only
its SHA-256 hash with a short expiry, and show ``t.me/<bot>?start=<token>``. When
they open it, Telegram's ``/start <token>`` reaches our bot webhook; we look the
hash up, mark it used (single-use), and record the chat id as a Telegram push
subscription. The plaintext token is never stored.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live import TelegramLinkToken

# Deep-link tokens are short-lived: the user is expected to click straight away.
TOKEN_TTL = timedelta(minutes=15)


def hash_token(token: str) -> str:
    """SHA-256 hex of a link token. Only the hash is ever persisted."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_link_token(
    session: AsyncSession, *, user_id: uuid.UUID, now: datetime | None = None
) -> tuple[str, datetime]:
    """Mint a single-use link token for ``user_id``. Returns (plaintext, expiry)."""
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(24)
    expires_at = now + TOKEN_TTL
    session.add(
        TelegramLinkToken(user_id=user_id, token_hash=hash_token(token), expires_at=expires_at)
    )
    await session.flush()
    return token, expires_at


async def consume_link_token(
    session: AsyncSession, token: str, *, now: datetime | None = None
) -> uuid.UUID | None:
    """Resolve a link token to its user and burn it. Returns ``None`` if the token
    is unknown, already used, or expired."""
    now = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(TelegramLinkToken).where(TelegramLinkToken.token_hash == hash_token(token))
        )
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now:
        return None
    row.used_at = now
    await session.flush()
    return row.user_id
