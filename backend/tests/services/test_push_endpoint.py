"""SSRF guard for Web Push endpoints (app/services/live/push_endpoint.py):
endpoint shape and push-service allowlist, every non-public address class
(IPv4 and IPv6, including IPv4 embedded in IPv6), and DNS answers."""

from __future__ import annotations

import ipaddress

import pytest
from app.core.config import get_settings
from app.services.live.push_endpoint import (
    EndpointUnresolvable,
    NonPublicAddress,
    UnsafeEndpoint,
    check_endpoint,
    is_public_ip,
    pinned_url,
    resolve_public,
    validate_endpoint_url,
)

ALLOWED = get_settings().webpush_allowed_host_list

# Real-world endpoint shapes, one per browser family.
REAL_ENDPOINTS = {
    "chrome-fcm-send": (
        "https://fcm.googleapis.com/fcm/send/dXJ0aWNsZTpBUEE5MWJIcVQw:"
        "APA91bHqT0eW8Kcf5Ff6N1m0aZ9pLr2c"
    ),
    "chrome-fcm-wp": "https://fcm.googleapis.com/wp/dXJ0aWNsZTpBUEE5MWJIcVQwZVc4S2Nm",
    "firefox": (
        "https://updates.push.services.mozilla.com/wpush/v2/"
        "gAAAAABmZ2NvZGVfZXhhbXBsZV90b2tlbl9mb3JfdGVzdHM"
    ),
    "safari": "https://web.push.apple.com/QGyWzDYS3gCJmWqVd2fP0xDr2XcgLhkU0Ns5d6vO",
    "edge": (
        "https://wns2-par02p.notify.windows.com/w/?token=BQYAAABxTmVwZXhhbXBsZXRva2VuZm9ydGVzdHM%3d"
    ),
}


@pytest.mark.parametrize("url", REAL_ENDPOINTS.values(), ids=REAL_ENDPOINTS.keys())
def test_real_push_service_endpoints_pass(url: str) -> None:
    host = validate_endpoint_url(url, ALLOWED)
    assert host == url.split("/")[2]


def test_host_is_compared_case_insensitively() -> None:
    assert validate_endpoint_url("https://FCM.GoogleAPIs.com/wp/x", ALLOWED) == (
        "fcm.googleapis.com"
    )


@pytest.mark.parametrize(
    "url",
    [
        # Lookalike hosts.
        "https://fcm.googleapis.com.evil.example/fcm/send/x",
        "https://fcm-googleapis.com/fcm/send/x",
        "https://fcmgoogleapis.com/fcm/send/x",
        "https://googleapis.com/fcm/send/x",
        "https://evil.example/fcm.googleapis.com/x",
        "https://updates.push.services.mozilla.com.attacker.net/wpush/v2/x",
        "https://push.services.mozilla.com/wpush/v2/x",
        "https://web.push.apple.com.attacker.net/x",
        "https://evilnotify.windows.com/w/?token=x",
        "https://notify.windows.com/w/?token=x",  # the suffix itself is not a push host
        "https://wns2-par02p.notify.windows.com.evil.example/w/",
        "https://xn--fcm-googleapis-xyz.com/x",
        # Userinfo tricks.
        "https://fcm.googleapis.com@evil.example/x",
        "https://user:pass@fcm.googleapis.com/x",
        # Scheme, port, canonical form.
        "http://fcm.googleapis.com/fcm/send/x",
        "ftp://fcm.googleapis.com/x",
        "//fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
        "https://fcm.googleapis.com:0/x",
        "https://fcm.googleapis.com:99999/x",
        "https://fcm.googleapis.com./x",
        # Internal services and IP literals.
        "http://mlflow:5000/api/2.0/mlflow/experiments/search",
        "https://api:8000/admin/ping",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/x",
        "https://127.0.0.1/x",
        # Not ASCII, whitespace, control characters, too long.
        "https://fсm.googleapis.com/x",  # Cyrillic "с"
        "https://fcm.googleapis.com/x y",
        "https://fcm.googleapis.com/x\r\nHost: evil",
        "https://fcm.googleapis.com/" + "a" * 600,
        "",
    ],
)
def test_lookalike_and_malformed_endpoints_fail(url: str) -> None:
    with pytest.raises(UnsafeEndpoint) as excinfo:
        validate_endpoint_url(url, ALLOWED)
    # The message never echoes the endpoint.
    assert url not in str(excinfo.value) or url == ""


def test_allowlist_is_configurable() -> None:
    assert validate_endpoint_url("https://push.example.org/x", ["push.example.org"]) == (
        "push.example.org"
    )
    with pytest.raises(UnsafeEndpoint):
        validate_endpoint_url(REAL_ENDPOINTS["chrome-fcm-wp"], ["push.example.org"])


NON_PUBLIC = {
    # IPv4
    "rfc1918-10": "10.0.0.5",
    "rfc1918-172": "172.16.3.4",
    "rfc1918-192": "192.168.1.1",
    "docker-network": "172.29.89.10",
    "loopback": "127.0.0.1",
    "loopback-other": "127.8.9.10",
    "unspecified": "0.0.0.0",  # noqa: S104 - a DNS answer under test, not a bind
    "this-network": "0.1.2.3",
    "link-local": "169.254.1.1",
    "metadata-aws-gcp-azure": "169.254.169.254",
    "metadata-alibaba": "100.100.100.200",
    "cgnat": "100.64.0.1",
    "benchmark": "198.18.0.1",
    "test-net-1": "192.0.2.10",
    "test-net-3": "203.0.113.7",
    "ietf-protocol": "192.0.0.8",
    "multicast": "224.0.0.251",
    "reserved": "240.0.0.1",
    "broadcast": "255.255.255.255",
    # IPv6
    "v6-loopback": "::1",
    "v6-unspecified": "::",
    "v6-link-local": "fe80::1",
    "v6-link-local-scoped": "fe80::1%eth0",
    "v6-ula": "fd12:3456:789a::1",
    "v6-metadata-aws": "fd00:ec2::254",
    "v6-site-local": "fec0::1",
    "v6-multicast": "ff02::1",
    "v6-documentation": "2001:db8::1",
    "v6-mapped-private": "::ffff:10.0.0.5",
    "v6-mapped-loopback": "::ffff:127.0.0.1",
    "v6-mapped-metadata": "::ffff:169.254.169.254",
    "v6-nat64-private": "64:ff9b::a00:5",
    "v6-nat64-metadata": "64:ff9b::a9fe:a9fe",
    "v6-6to4-private": "2002:a00:5::1",
    "v6-teredo": "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    "v6-discard": "100::1",
}

PUBLIC = {
    "v4-google": "142.250.74.10",
    "v4-mozilla": "34.117.65.55",
    "v6-google": "2a00:1450:4001:82a::200a",
    "v6-mapped-public": "::ffff:142.250.74.10",
    "v6-nat64-public": "64:ff9b::8efa:4a0a",
}


@pytest.mark.parametrize("address", NON_PUBLIC.values(), ids=NON_PUBLIC.keys())
def test_non_public_addresses_are_refused(address: str) -> None:
    assert not is_public_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", PUBLIC.values(), ids=PUBLIC.keys())
def test_public_addresses_are_accepted(address: str) -> None:
    assert is_public_ip(ipaddress.ip_address(address))


def _dns(monkeypatch: pytest.MonkeyPatch, answer: list[str]) -> None:
    async def fake(host: str) -> list[str]:
        return answer

    monkeypatch.setattr("app.services.live.push_endpoint._getaddrinfo", fake)


@pytest.mark.asyncio
@pytest.mark.parametrize("address", NON_PUBLIC.values(), ids=NON_PUBLIC.keys())
async def test_dns_answer_in_a_non_public_class_is_refused(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    _dns(monkeypatch, [address])
    # NonPublicAddress: refused at subscribe time, retried (not pruned) at send time.
    with pytest.raises(NonPublicAddress):
        await resolve_public("fcm.googleapis.com")


@pytest.mark.asyncio
async def test_one_private_answer_among_public_ones_refuses_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _dns(monkeypatch, ["142.250.74.10", "2a00:1450:4001:82a::200a", "10.0.0.5"])
    with pytest.raises(UnsafeEndpoint):
        await resolve_public("fcm.googleapis.com")


@pytest.mark.asyncio
async def test_pins_ipv4_first_then_lowest(monkeypatch: pytest.MonkeyPatch) -> None:
    _dns(monkeypatch, ["2a00:1450:4001:82a::200a", "142.250.74.11", "142.250.74.10"])
    assert str(await resolve_public("fcm.googleapis.com")) == "142.250.74.10"


@pytest.mark.asyncio
async def test_empty_or_failed_dns_is_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _dns(monkeypatch, [])
    with pytest.raises(EndpointUnresolvable):
        await resolve_public("fcm.googleapis.com")

    async def fail(host: str) -> list[str]:
        raise OSError("Temporary failure in name resolution")

    monkeypatch.setattr("app.services.live.push_endpoint._getaddrinfo", fail)
    with pytest.raises(EndpointUnresolvable):
        await resolve_public("fcm.googleapis.com")


@pytest.mark.asyncio
async def test_check_endpoint_refuses_before_dns_for_a_bad_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looked_up: list[str] = []

    async def fake(host: str) -> list[str]:
        looked_up.append(host)
        return ["142.250.74.10"]

    monkeypatch.setattr("app.services.live.push_endpoint._getaddrinfo", fake)
    with pytest.raises(UnsafeEndpoint):
        await check_endpoint("https://evil.example/x", ALLOWED)
    assert looked_up == []

    host, ip = await check_endpoint(REAL_ENDPOINTS["firefox"], ALLOWED)
    assert (host, str(ip)) == ("updates.push.services.mozilla.com", "142.250.74.10")


def test_pinned_url_keeps_path_and_query() -> None:
    edge = REAL_ENDPOINTS["edge"]
    v4 = pinned_url(edge, ipaddress.ip_address("20.42.1.2"))
    assert v4 == "https://20.42.1.2/w/?token=BQYAAABxTmVwZXhhbXBsZXRva2VuZm9ydGVzdHM%3d"
    v6 = pinned_url(REAL_ENDPOINTS["chrome-fcm-wp"], ipaddress.ip_address("2a00:1450::a"))
    assert v6.startswith("https://[2a00:1450::a]/wp/")
