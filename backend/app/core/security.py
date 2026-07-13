"""Password hashing, JWT issuing/verification, TOTP and token helpers.

Argon2id parameters (configurable, defaults in ``Settings``)
------------------------------------------------------------
- ``time_cost = 3`` iterations
- ``memory_cost = 65536 KiB`` (64 MiB)
- ``parallelism = 4`` lanes

Rationale: the OWASP Password Storage Cheat Sheet recommends Argon2id with a
minimum of m=19 MiB, t=2, p=1. We deliberately go stronger — 64 MiB and 3
iterations across 4 lanes — which costs roughly 50-100 ms per hash on a modern
multi-core VPS. That keeps interactive login latency acceptable while making
offline GPU/ASIC cracking substantially more expensive (memory-hardness is the
point of Argon2id). Parameters live in config so they can be raised over time;
``verify_password`` reports when a stored hash should be upgraded.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

ACCESS_TOKEN_TYPE = "access"  # noqa: S105  # nosec B105  (token type label)


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
        type=Type.ID,
    )


# A stable dummy hash used to equalise timing when authenticating a non-existent
# account, so responses do not leak whether an email is registered.
_DUMMY_HASH = _hasher().hash("bp-dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_dummy_password() -> None:
    """Spend comparable CPU to a real verify when the user does not exist."""
    try:
        _hasher().verify(_DUMMY_HASH, "wrong")
    except VerifyMismatchError:
        pass


def needs_rehash(password_hash: str) -> bool:
    return _hasher().check_needs_rehash(password_hash)


# --- JWT access tokens ------------------------------------------------------


def create_access_token(*, subject: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_minutes)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token. Raises ``jwt.PyJWTError`` on failure."""
    settings = get_settings()
    claims: dict[str, Any] = jwt.decode(
        token, settings.secret_key, algorithms=[settings.jwt_algorithm]
    )
    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


# --- Opaque tokens (refresh, email verification) ----------------------------


def generate_opaque_token() -> str:
    """A high-entropy, URL-safe opaque token (never a JWT)."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Deterministic SHA-256 hex digest for storing/looking up opaque tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- CSRF -------------------------------------------------------------------


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


# --- TOTP (admin 2FA) -------------------------------------------------------


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, account_email: str) -> str:
    settings = get_settings()
    return pyotp.TOTP(secret).provisioning_uri(name=account_email, issuer_name=settings.app_name)


def verify_totp(secret: str, code: str) -> bool:
    # valid_window=1 tolerates one 30s step of clock drift.
    return pyotp.TOTP(secret).verify(code, valid_window=1)
