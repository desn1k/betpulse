"""Unit tests for client IP resolution and the TRUSTED_PROXY_CIDRS setting."""

from __future__ import annotations

from typing import Any

import pytest
from app.core.client_ip import (
    UNKNOWN_CLIENT,
    parse_trusted_proxies,
    rate_limit_bucket,
    resolve_client_ip,
)
from app.core.config import Settings
from pydantic import ValidationError

TRUSTED = parse_trusted_proxies("172.29.89.10/32,172.29.89.11/32,fd00:bb::/64")
WEB = "172.29.89.10"
CADDY = "172.29.89.11"


@pytest.mark.parametrize(
    ("peer", "forwarded_for", "expected"),
    [
        # Untrusted peer: the header is ignored entirely.
        ("203.0.113.9", "198.51.100.7", "203.0.113.9"),
        ("203.0.113.9", None, "203.0.113.9"),
        # Trusted peer: the client it reports.
        (WEB, "198.51.100.7", "198.51.100.7"),
        # A client-supplied hop on the left is never trusted.
        (WEB, "6.6.6.6, 198.51.100.7", "198.51.100.7"),
        # Trusted hops are skipped right to left.
        (WEB, f"198.51.100.7, {CADDY}", "198.51.100.7"),
        (WEB, f"6.6.6.6, 198.51.100.7, {CADDY}", "198.51.100.7"),
        # Trusted peer without a header: the peer itself.
        (WEB, None, WEB),
        (WEB, "", WEB),
        # IPv6 is normalised; IPv4-mapped addresses unwrap to IPv4.
        (WEB, "2001:DB8:0:0::1", "2001:db8::1"),
        (WEB, "::ffff:198.51.100.7", "198.51.100.7"),
        ("fd00:bb::5", "2001:db8::1", "2001:db8::1"),
        ("::ffff:203.0.113.9", "198.51.100.7", "203.0.113.9"),
    ],
)
def test_resolve_client_ip(peer: str, forwarded_for: str | None, expected: str) -> None:
    assert resolve_client_ip(peer, forwarded_for, TRUSTED) == expected


@pytest.mark.parametrize(
    "forwarded_for",
    ["not-an-ip", "unknown", "198.51.100.7:443", "[2001:db8::1]", " , ", "198.51.100.300"],
)
def test_malformed_forwarded_for_falls_back_to_the_trusted_hop(forwarded_for: str) -> None:
    assert resolve_client_ip(WEB, forwarded_for, TRUSTED) == WEB


def test_malformed_hop_stops_the_walk() -> None:
    # Garbage left of a valid client is never reached; garbage to its right
    # (i.e. reported by our own proxy) means the chain cannot be trusted.
    assert resolve_client_ip(WEB, "junk, 198.51.100.7", TRUSTED) == "198.51.100.7"
    assert resolve_client_ip(WEB, f"198.51.100.7, junk, {CADDY}", TRUSTED) == CADDY


def test_missing_or_invalid_peer_is_unknown() -> None:
    assert resolve_client_ip(None, "198.51.100.7", TRUSTED) == UNKNOWN_CLIENT
    assert resolve_client_ip("testclient", "198.51.100.7", TRUSTED) == UNKNOWN_CLIENT


def test_rate_limit_bucket_groups_ipv6_by_64() -> None:
    assert rate_limit_bucket("198.51.100.7") == "198.51.100.7"
    assert rate_limit_bucket("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert rate_limit_bucket("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert rate_limit_bucket("2001:db8:1:3::1") == "2001:db8:1:3::/64"
    assert rate_limit_bucket(UNKNOWN_CLIENT) == UNKNOWN_CLIENT


@pytest.mark.parametrize("raw", ["0.0.0.0/0", "::/0", "10.0.0.1/8", "not-a-cidr", "10.0.0.0/33"])
def test_invalid_trusted_proxies_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        parse_trusted_proxies(raw)


# --- Settings -----------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "production",
        "secret_key": "a" * 64,
        "data_encryption_key": "b" * 64,
        "cors_allowed_origins": "https://betpulse.example",
        "trusted_proxy_cidrs": "172.29.89.10/32,172.29.89.11/32",
    }
    values.update(overrides)
    return Settings(**values)


def test_development_defaults_to_loopback() -> None:
    settings = _settings(environment="development", trusted_proxy_cidrs=None)
    assert [str(n) for n in settings.trusted_proxy_networks] == ["127.0.0.1/32", "::1/128"]


def test_production_accepts_pinned_proxies() -> None:
    assert [str(n) for n in _settings().trusted_proxy_networks] == [
        "172.29.89.10/32",
        "172.29.89.11/32",
    ]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "must be set explicitly"),
        ("", "must list the internal proxy addresses"),
        ("0.0.0.0/0", "would trust every address"),
        ("8.8.8.0/24", "is not a private network"),
        ("10.0.0.0/8", "too broad"),
        ("fd00::/8", "too broad"),
        ("172.29.89.10", None),  # a bare address is a /32 and is fine
    ],
)
def test_production_trusted_proxy_validation(value: str | None, message: str | None) -> None:
    if message is None:
        assert _settings(trusted_proxy_cidrs=value).trusted_proxy_networks
        return
    with pytest.raises(ValidationError, match=message):
        _settings(trusted_proxy_cidrs=value)


def test_malformed_cidrs_fail_outside_production_too() -> None:
    with pytest.raises(ValidationError, match="invalid network"):
        _settings(environment="development", trusted_proxy_cidrs="172.29.89.0/16")
