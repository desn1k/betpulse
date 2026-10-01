"""Known-answer regressions for crypto dependency upgrades.

The constants below were produced with cryptography 48.0.1 and PyJWT 2.13.0
using the test keys from ``conftest.py``. They must keep verifying after any
upgrade: Fernet tokens are data encrypted at rest, and access tokens issued
before a deploy must stay valid until they expire.
"""

from __future__ import annotations

from app.core import security
from app.core.config import get_settings
from app.core.crypto import decrypt_secret

# Fernet token for "known-answer-secret-v1" under DATA_ENCRYPTION_KEY="1" * 64.
LEGACY_FERNET_TOKEN = (
    "gAAAAABqvrwDR3I_Lank5ss6UrMHAz_vbH4nMtdMEBdTt6bqoFuWbJrJ7ZFsPqYfZhJvPRJc"
    "HBv6SZqcArBtgxe8gV26gLfGmQi4S6PFproYGXECuRZkpWM="
)

# HS256 access token under SECRET_KEY="0" * 64; expires 2100-01-01.
LEGACY_ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJrbm93bi1hbnN3ZXItdXNlciIsInJvbGUiOiJhZG1pbiIsInR5cGUiOiJhY2Nlc3Mi"
    "LCJpYXQiOjE3OTAwMDAwMDAsImV4cCI6NDEwMjQ0NDgwMCwianRpIjoiMDAwMDAwMDAwMDAwMDAw"
    "MDAwMDAwMDAwMDAwMDAwMDAifQ."
    "nyKUZE4QUJLu76BkxRvM345eK67WRhqtcAwiKRCS6PY"
)


def test_legacy_fernet_token_still_decrypts() -> None:
    assert get_settings().data_encryption_key == "1" * 64
    assert decrypt_secret(LEGACY_FERNET_TOKEN) == "known-answer-secret-v1"


def test_legacy_access_token_still_decodes() -> None:
    assert get_settings().secret_key == "0" * 64
    claims = security.decode_access_token(LEGACY_ACCESS_TOKEN)
    assert claims == {
        "sub": "known-answer-user",
        "role": "admin",
        "type": security.ACCESS_TOKEN_TYPE,
        "iat": 1790000000,
        "exp": 4102444800,
        "jti": "0" * 32,
    }
