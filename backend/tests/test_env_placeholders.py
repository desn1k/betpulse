"""Placeholder secrets never reach production.

Docker Compose reads an `env_file` line such as `KEY=   # comment` as the
value `# comment` (an empty value followed by an inline comment). With the
old .env.example that gave ADMIN_PASSWORD, the VAPID keys and others the
comment text, i.e. a value published in the repository. These tests pin the
three defences: no inline comments in .env.example, production settings that
reject placeholder or low-entropy secrets, and a bootstrap that refuses to
create the first admin with such a password.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

import pytest
from app.bootstrap import _create_admin
from app.core.config import Settings, is_placeholder_secret
from app.models.user import User, UserRole
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
GOOD_SECRET = secrets.token_hex(32)
GOOD_KEY = secrets.token_hex(32)


def _env_lines() -> list[str]:
    return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def _comment_texts() -> list[str]:
    return [line.strip() for line in _env_lines() if line.strip().startswith("#")]


def _prod(**overrides: str) -> Settings:
    values = {
        "environment": "production",
        "secret_key": GOOD_SECRET,
        "data_encryption_key": GOOD_KEY,
        "trusted_proxy_cidrs": "172.29.89.10/32",
        "cors_allowed_origins": "https://app.example.test",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# --- .env.example -------------------------------------------------------------


def test_env_example_has_no_inline_comments() -> None:
    offenders = [
        line
        for line in _env_lines()
        if re.match(r"^[A-Z0-9_]+=", line) and re.search(r"\s#", line.split("=", 1)[1])
    ]
    assert offenders == [], "inline comments become values in Compose env files"


def test_every_documented_comment_and_example_is_a_placeholder() -> None:
    texts = _comment_texts()
    assert texts, "expected comments in .env.example"
    for text in texts:
        assert is_placeholder_secret(text), text
    for example in ("change-me", "changeme", "password", "secret", "admin@example.com"):
        assert is_placeholder_secret(example), example


def test_real_random_secrets_are_not_placeholders() -> None:
    assert not is_placeholder_secret(secrets.token_hex(32))
    assert not is_placeholder_secret(secrets.token_urlsafe(24))


# --- production settings ----------------------------------------------------------


def test_production_accepts_real_secrets() -> None:
    assert _prod().is_production


@pytest.mark.parametrize(
    "value",
    [
        "# openssl rand -hex 32 — signs JWT access tokens",  # the old inline comment
        "a" * 64,  # long enough, no entropy
        "abababababababababababababababababababababababababab",
    ],
)
def test_production_rejects_placeholder_secret_key(value: str) -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _prod(secret_key=value)


@pytest.mark.parametrize("value", ["z" * 64, "# openssl rand -hex 32", "a" * 64])
def test_production_rejects_unusable_data_encryption_key(value: str) -> None:
    with pytest.raises(ValueError, match="DATA_ENCRYPTION_KEY"):
        _prod(data_encryption_key=value)


@pytest.mark.parametrize(
    "value",
    ["# leave empty to auto-generate a one-time password", "change-me", "short"],
)
def test_production_rejects_placeholder_admin_password(value: str) -> None:
    with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
        _prod(admin_password=value)


def test_production_allows_an_unset_admin_password_at_startup() -> None:
    # Only `create-admin` needs it; the API and workers start without it.
    assert _prod(admin_password="").admin_password == ""


# --- create-admin -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "password",
    ["", "# leave empty to auto-generate a one-time password", "too-short", "change-me"],
)
async def test_production_create_admin_refuses_placeholder_passwords(
    password: str,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_password", password)

    assert await _create_admin(force=False) == 2
    assert "ADMIN_PASSWORD" in capsys.readouterr().err
    assert await session.scalar(select(User).where(User.role == UserRole.admin)) is None


@pytest.mark.asyncio
async def test_production_create_admin_accepts_an_explicit_strong_password(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    password = secrets.token_urlsafe(18)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_password", password)

    assert await _create_admin(force=False) == 0
    assert password not in capsys.readouterr().out
    assert await session.scalar(select(User).where(User.role == UserRole.admin)) is not None
