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


def _compose_values() -> dict[str, str]:
    """`KEY=VALUE` lines as Compose reads an unquoted env_file value: the rest
    of the line, with ` #...` cut only when whitespace precedes the `#`."""
    values: dict[str, str] = {}
    for line in _env_lines():
        match = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if match:
            values[match.group(1)] = re.split(r"\s+#", match.group(2), maxsplit=1)[0].strip()
    return values


def test_no_env_example_value_reads_as_a_comment() -> None:
    values = _compose_values()
    assert values, "expected KEY=VALUE lines in .env.example"
    # `KEY=#note` (no space before #) is the literal value `#note` for Compose.
    leaked = sorted(key for key, value in values.items() if value.startswith("#"))
    assert leaked == []


def test_documented_examples_and_leaked_comments_are_placeholders() -> None:
    for example in ("change-me", "changeme", "password", "secret", "admin@example.com"):
        assert is_placeholder_secret(example), example
    # Every comment of the file is what such a value would be if it leaked.
    for text in _comment_texts():
        assert is_placeholder_secret(text), text


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


# --- error output never carries secrets ------------------------------------------


def test_settings_errors_do_not_echo_the_input_values() -> None:
    leaked_secret = secrets.token_hex(32)
    with pytest.raises(ValueError) as exc:
        _prod(secret_key=leaked_secret, admin_password="#not-a-password")
    assert "ADMIN_PASSWORD" in str(exc.value)
    assert leaked_secret not in str(exc.value)
    assert "#not-a-password" not in str(exc.value)


def test_create_admin_cli_exits_2_and_prints_no_secret_for_a_placeholder() -> None:
    import os
    import subprocess
    import sys

    secret = secrets.token_hex(32)
    env = {
        **os.environ,
        "ENVIRONMENT": "production",
        "SECRET_KEY": secret,
        "DATA_ENCRYPTION_KEY": secrets.token_hex(32),
        "TRUSTED_PROXY_CIDRS": "172.29.89.10/32",
        "CORS_ALLOWED_ORIGINS": "https://app.example.test",
        "ADMIN_PASSWORD": "# leave empty to auto-generate a one-time password",
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-m", "app.bootstrap", "create-admin"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "ADMIN_PASSWORD" in output
    assert secret not in output and "leave empty to auto-generate" not in output


# --- patterned values that fool a Shannon estimate -------------------------------

PATTERNED_SECRET_KEYS = [
    "abcdefghijklmnop" * 2,  # review example: ~128 bits by Shannon, still a pattern
    "0123456789abcdef" * 4,  # review example: valid hex, ~256 bits by Shannon
    "Kx7Pq2Lm" * 8,  # periodic, no run
    "q9" + "abcdefghij" + secrets.token_hex(20),  # random tail, but a keyboard run
]


@pytest.mark.parametrize("value", PATTERNED_SECRET_KEYS)
def test_patterned_values_are_placeholders(value: str) -> None:
    assert is_placeholder_secret(value)


@pytest.mark.parametrize("value", PATTERNED_SECRET_KEYS)
def test_production_rejects_patterned_secret_key(value: str) -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _prod(secret_key=value)


@pytest.mark.parametrize("value", ["0123456789abcdef" * 4, "deadbeef" * 8, "fedcba9876543210" * 4])
def test_production_rejects_patterned_data_encryption_key(value: str) -> None:
    with pytest.raises(ValueError, match="DATA_ENCRYPTION_KEY"):
        _prod(data_encryption_key=value)


def test_few_distinct_characters_in_a_long_value_are_rejected() -> None:
    value = "qwrtzpkmvwqtrzkpmvqzwtrkmpvzqwkt"  # 32 long, 9 distinct, no block, no run
    assert len(value) == 32 and len(set(value)) == 9
    assert is_placeholder_secret(value)


def test_admin_password_with_a_keyboard_run_is_rejected() -> None:
    with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
        _prod(admin_password="Password12345678")


def test_random_secrets_have_no_false_positives() -> None:
    """10k values per generator shape, from a seeded RNG so the test is
    deterministic. Same alphabets and lengths as secrets.token_hex(32) and
    secrets.token_urlsafe(32), the commands the docs tell operators to use."""
    import base64
    import random

    rng = random.Random(20261004)  # noqa: S311 - test data, not secrets
    rejected: list[str] = []
    for _ in range(10_000):
        raw = rng.randbytes(32)
        for value in (raw.hex(), base64.urlsafe_b64encode(raw).rstrip(b"=").decode()):
            if is_placeholder_secret(value):
                rejected.append(value)
    assert rejected == []
    # And a few real CSPRNG draws, end to end through production validation.
    for _ in range(50):
        assert _prod(
            secret_key=secrets.token_urlsafe(32), data_encryption_key=secrets.token_hex(32)
        )
