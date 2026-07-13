"""Unit tests for password hashing, JWT, TOTP, tokens and secret encryption."""

from __future__ import annotations

import time

import jwt
import pyotp
import pytest
from app.core import security
from app.core.config import get_settings
from app.core.crypto import SecretEncryptionError, decrypt_secret, encrypt_secret


def test_password_hash_verifies_and_rejects() -> None:
    h = security.hash_password("correct horse battery staple")
    assert security.verify_password(h, "correct horse battery staple") is True
    assert security.verify_password(h, "wrong password") is False


def test_password_hashes_are_salted() -> None:
    a = security.hash_password("same-password-123")
    b = security.hash_password("same-password-123")
    assert a != b


def test_access_token_roundtrip() -> None:
    token = security.create_access_token(subject="user-123", role="admin")
    claims = security.decode_access_token(token)
    assert claims["sub"] == "user-123"
    assert claims["role"] == "admin"
    assert claims["type"] == security.ACCESS_TOKEN_TYPE


def test_decode_rejects_wrong_type() -> None:
    settings = get_settings()
    forged = jwt.encode(
        {"sub": "x", "type": "refresh", "exp": int(time.time()) + 60},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_access_token(forged)


def test_decode_rejects_expired() -> None:
    settings = get_settings()
    expired = jwt.encode(
        {"sub": "x", "type": security.ACCESS_TOKEN_TYPE, "exp": int(time.time()) - 5},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(expired)


def test_totp_verifies_expected_code() -> None:
    secret = security.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert security.verify_totp(secret, code) is True
    assert security.verify_totp(secret, "000000") is False


def test_opaque_tokens_are_unique_and_hash_is_stable() -> None:
    t1 = security.generate_opaque_token()
    t2 = security.generate_opaque_token()
    assert t1 != t2
    assert security.hash_token(t1) == security.hash_token(t1)
    assert len(security.hash_token(t1)) == 64


def test_constant_time_equals() -> None:
    assert security.constant_time_equals("abc", "abc") is True
    assert security.constant_time_equals("abc", "abd") is False


def test_secret_encryption_roundtrip() -> None:
    token = encrypt_secret("super-secret-value")
    assert token != "super-secret-value"
    assert decrypt_secret(token) == "super-secret-value"


def test_decrypt_rejects_tampered_token() -> None:
    with pytest.raises(SecretEncryptionError):
        decrypt_secret("not-a-valid-fernet-token")
