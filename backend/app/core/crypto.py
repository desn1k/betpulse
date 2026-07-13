"""Symmetric encryption of secrets at rest.

A single application-level key (``DATA_ENCRYPTION_KEY``) encrypts sensitive
values before they touch the database — starting with TOTP secrets in Phase 2,
and reused for provider / LLM API keys in later phases. The key is a 64-char
hex string (``openssl rand -hex 32``); we derive a urlsafe-base64 Fernet key
from its 32 raw bytes.

Losing the key means losing the ability to decrypt every stored secret, so it
is backed up separately from the database (see ``.env.example``).
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretEncryptionError(RuntimeError):
    """Raised when a secret cannot be encrypted or decrypted."""


def _fernet_key_from_hex(hex_key: str) -> bytes:
    try:
        raw = bytes.fromhex(hex_key)
    except ValueError as exc:  # noqa: TRY003
        raise SecretEncryptionError("DATA_ENCRYPTION_KEY must be valid hex") from exc
    if len(raw) != 32:
        raise SecretEncryptionError("DATA_ENCRYPTION_KEY must decode to 32 bytes")
    return base64.urlsafe_b64encode(raw)


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.data_encryption_key:
        raise SecretEncryptionError("DATA_ENCRYPTION_KEY is not configured")
    return Fernet(_fernet_key_from_hex(settings.data_encryption_key))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret, returning a urlsafe token string."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt_secret`."""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # noqa: TRY003
        raise SecretEncryptionError("could not decrypt secret") from exc
