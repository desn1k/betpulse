"""Unit tests for client IP resolution and the TRUSTED_PROXY_CIDRS setting."""

from __future__ import annotations

import secrets
from typing import Any

import pytest
from app.core.client_ip import (
    UNKNOWN_CLIENT,
    internal_client,
    parse_networks,
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
        # Real random keys: production rejects placeholder or low-entropy ones.
        "secret_key": secrets.token_hex(32),
        "data_encryption_key": secrets.token_hex(32),
        "cors_allowed_origins": "https://betpulse.example",
        "trusted_proxy_cidrs": "172.29.89.10/32,172.29.89.11/32",
        "internal_network_cidrs": "172.29.89.0/24",
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


# --- Internal-network clients (F5) ----------------------------------------------

INTERNAL = parse_networks("172.29.89.0/24,fd42:b7e1:5a29:89::/64", "INTERNAL_NETWORK_CIDRS")
DUAL_STACK_TRUSTED = parse_trusted_proxies(
    "172.29.89.10/32,172.29.89.11/32,fd42:b7e1:5a29:89::10/128,fd42:b7e1:5a29:89::11/128"
)
WEB6 = "fd42:b7e1:5a29:89::10"


def test_bff_over_ipv6_vouches_for_the_client() -> None:
    # The BFF may reach the API over IPv6 on the dual-stack network: its pinned
    # IPv6 address is trusted like its IPv4 one.
    assert resolve_client_ip(WEB6, "2001:db8::7", DUAL_STACK_TRUSTED) == "2001:db8::7"
    assert resolve_client_ip(WEB6, "198.51.100.7", DUAL_STACK_TRUSTED) == "198.51.100.7"


@pytest.mark.parametrize(
    ("peer", "forwarded_for", "expected"),
    [
        # The bridge gateway vouched for by our own proxy: the F5 collapse.
        (WEB, "172.29.89.1", "172.29.89.1"),
        (WEB6, "fd42:b7e1:5a29:89::1", "fd42:b7e1:5a29:89::1"),
        # An internal hop the API does not trust (e.g. caddy's IPv6 address
        # left out of TRUSTED_PROXY_CIDRS) is an internal identity as well.
        (WEB, "fd42:b7e1:5a29:89::11", "fd42:b7e1:5a29:89::11"),
        # Real clients, IPv4 and IPv6, and IPv4-mapped forms of them.
        (WEB, "198.51.100.7", None),
        (WEB6, "2001:db8::7", None),
        (WEB, "::ffff:198.51.100.7", None),
        # No header: the proxy's own request (deploy.sh's /api/ready probe from
        # inside web), not a client's.
        (WEB, None, None),
        (WEB, "", None),
        # An untrusted peer's header is never a client identity.
        ("203.0.113.9", "172.29.89.1", None),
        (None, "172.29.89.1", None),
    ],
)
def test_internal_client(peer: str | None, forwarded_for: str | None, expected: str | None) -> None:
    assert internal_client(peer, forwarded_for, DUAL_STACK_TRUSTED, INTERNAL) == expected


def test_internal_client_is_off_without_internal_networks() -> None:
    assert internal_client(WEB, "172.29.89.1", DUAL_STACK_TRUSTED, ()) is None


@pytest.mark.parametrize("raw", ["0.0.0.0/0", "::/0", "not-a-cidr", "10.0.0.1/8"])
def test_invalid_internal_networks_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="INTERNAL_NETWORK_CIDRS"):
        parse_networks(raw, "INTERNAL_NETWORK_CIDRS")


def test_production_requires_internal_networks() -> None:
    with pytest.raises(ValidationError, match="INTERNAL_NETWORK_CIDRS must be set"):
        _settings(internal_network_cidrs=None)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("8.8.8.0/24", "is not a private network"),
        # Every trusted proxy must live on the internal network.
        ("172.29.90.0/24", "172.29.89.10/32 is outside INTERNAL_NETWORK_CIDRS"),
    ],
)
def test_production_internal_network_validation(value: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(internal_network_cidrs=value)


def test_production_accepts_the_dual_stack_network() -> None:
    settings = _settings(
        trusted_proxy_cidrs=(
            "172.29.89.10/32,172.29.89.11/32,fd42:b7e1:5a29:89::10/128,fd42:b7e1:5a29:89::11/128"
        ),
        internal_network_cidrs="172.29.89.0/24,fd42:b7e1:5a29:89::/64",
    )
    assert [str(n) for n in settings.internal_networks] == [
        "172.29.89.0/24",
        "fd42:b7e1:5a29:89::/64",
    ]


def test_development_has_no_internal_networks_by_default() -> None:
    assert _settings(environment="development", internal_network_cidrs=None).internal_networks == ()
