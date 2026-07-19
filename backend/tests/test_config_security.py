"""Production configuration security regressions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_rejects_wildcard_cors_with_credentials() -> None:
    with pytest.raises(ValidationError, match="explicit origins"):
        Settings(
            environment="production",
            secret_key="s" * 64,
            data_encryption_key="d" * 64,
            cors_allowed_origins="*",
        )


def test_development_keeps_wildcard_cors_available_for_local_tooling() -> None:
    settings = Settings(environment="development", cors_allowed_origins="*")

    assert settings.cors_origins == ["*"]
