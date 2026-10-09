"""Client IP resolution behind our reverse proxies.

Production traffic reaches the API through Caddy (Telegram webhook) or through
the Next.js BFF, so the direct peer is one of our own containers. Their
``X-Forwarded-For`` is honoured only when the peer is listed in
``TRUSTED_PROXY_CIDRS``; anything else — including a header sent straight to
the API by an untrusted client — is ignored and the peer address is used.

Within a trusted chain the client is the *right-most* address that is not a
trusted proxy. Left-hand entries are client-controlled and never trusted.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable
from functools import lru_cache

logger = logging.getLogger(__name__)

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

UNKNOWN_CLIENT = "unknown"

# Broadest prefixes accepted for a trusted proxy network in production: the
# list must name our internal proxy hosts, not a whole address range.
_MIN_PRODUCTION_PREFIX = {4: 16, 6: 48}
# IPv6 clients usually control a whole /64, so per-address limits are trivial
# to rotate around; rate-limit buckets use the /64 instead.
_IPV6_BUCKET_PREFIX = 64


@lru_cache(maxsize=16)
def parse_trusted_proxies(raw: str) -> tuple[IPNetwork, ...]:
    """Parse a comma-separated CIDR list. Raises ``ValueError`` on bad input."""
    networks: list[IPNetwork] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXY_CIDRS: invalid network {value!r}") from exc
        if network.prefixlen == 0:
            raise ValueError(f"TRUSTED_PROXY_CIDRS: {value} would trust every address")
        networks.append(network)
    return tuple(networks)


def validate_production_proxies(networks: Iterable[IPNetwork]) -> None:
    """Reject trust lists that are empty, public or too broad for production."""
    items = tuple(networks)
    if not items:
        raise ValueError("TRUSTED_PROXY_CIDRS must list the internal proxy addresses")
    for network in items:
        if not (network.is_private or network.is_loopback):
            raise ValueError(f"TRUSTED_PROXY_CIDRS: {network} is not a private network")
        if network.prefixlen < _MIN_PRODUCTION_PREFIX[network.version]:
            raise ValueError(f"TRUSTED_PROXY_CIDRS: {network} is too broad for production")


def _parse_ip(value: str) -> IPAddress | None:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted(address: IPAddress, trusted: tuple[IPNetwork, ...]) -> bool:
    return any(address in network for network in trusted)


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted: tuple[IPNetwork, ...]
) -> str:
    """Return the canonical client address for a request.

    ``peer`` is the TCP peer and ``forwarded_for`` the raw ``X-Forwarded-For``
    value. A malformed hop ends the walk and the last trusted hop (the proxy
    that relayed it) is used, so garbage never becomes a client identity.
    """
    peer_ip = _parse_ip(peer) if peer else None
    if peer_ip is None:
        return UNKNOWN_CLIENT
    if not forwarded_for or not _is_trusted(peer_ip, trusted):
        return peer_ip.compressed

    client = peer_ip
    for hop in reversed(forwarded_for.split(",")):
        hop_ip = _parse_ip(hop)
        if hop_ip is None:
            logger.warning("ignoring malformed X-Forwarded-For hop from trusted proxy")
            break
        client = hop_ip
        if not _is_trusted(hop_ip, trusted):
            break
    return client.compressed


def replay_subnet(client_ip: str) -> str | None:
    """The client's network for binding a refresh-token replay (F13 B): IPv4
    /24, IPv6 /64. ``None`` for an unknown or malformed address, which never
    matches anything."""
    address = _parse_ip(client_ip)
    if address is None:
        return None
    prefix = _IPV6_BUCKET_PREFIX if isinstance(address, ipaddress.IPv6Address) else 24
    return ipaddress.ip_network(f"{address}/{prefix}", strict=False).compressed


def rate_limit_bucket(client_ip: str) -> str:
    """Key for per-IP rate limits and guest quotas: IPv4 per address, IPv6 per /64."""
    address = _parse_ip(client_ip)
    if isinstance(address, ipaddress.IPv6Address):
        return ipaddress.ip_network(f"{address}/{_IPV6_BUCKET_PREFIX}", strict=False).compressed
    return client_ip
