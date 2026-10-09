"""Production configuration security regressions."""

from __future__ import annotations

import secrets

import pytest
from app.core.config import Settings


def test_production_rejects_wildcard_cors_with_credentials() -> None:
    with pytest.raises(ValueError, match="explicit origins"):
        Settings(
            environment="production",
            secret_key=secrets.token_hex(32),
            data_encryption_key=secrets.token_hex(32),
            cors_allowed_origins="*",
            trusted_proxy_cidrs="172.29.89.10/32",
            internal_network_cidrs="172.29.89.0/24",
        )


def test_development_keeps_wildcard_cors_available_for_local_tooling() -> None:
    settings = Settings(environment="development", cors_allowed_origins="*")

    assert settings.cors_origins == ["*"]
