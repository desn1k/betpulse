"""SSRF guard for Web Push endpoints.

A Web Push endpoint is a URL the browser hands us, and the server POSTs to it on
every swing push. Without checks a user could point it at an internal service
(``http://mlflow:5000``, ``http://api:8000``) or a cloud metadata address. So an
endpoint must:

- be ``https`` on the default port, with no userinfo, and name its host (never
  an IP literal);
- name a host on the push-service allowlist (``Settings.webpush_allowed_hosts``:
  an exact host, or a ``.suffix`` matching any subdomain);
- resolve **only** to public addresses: one private, loopback, link-local
  (metadata), CGNAT, multicast or reserved address, IPv4 or IPv6, including
  IPv4 embedded in IPv6 (mapped, NAT64, 6to4), refuses the whole endpoint.

The check runs when a subscription is stored and again before every send. The
sender then connects to the address it just checked (pinned; ``Host`` and TLS
SNI keep the original name, so the certificate is still verified for it), so a
DNS answer that changes between the check and the connect (rebinding) is never
followed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# The schema caps stored endpoints at the same length.
MAX_ENDPOINT_LENGTH = 512
DNS_TIMEOUT_SECONDS = 5.0

_CGNAT = ipaddress.IPv4Network("100.64.0.0/10")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


class UnsafeEndpoint(ValueError):
    """The endpoint may never be used: wrong shape, host not allowed, or a
    non-public address. The message never contains the endpoint."""


class EndpointUnresolvable(Exception):
    """DNS gave no answer (possibly transient)."""


def _ipv4_is_public(ip: ipaddress.IPv4Address) -> bool:
    return (
        ip.is_global
        and ip not in _CGNAT
        and not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    )


def is_public_ip(ip: IPAddress) -> bool:
    """True only for a globally routable unicast address."""
    if isinstance(ip, ipaddress.IPv4Address):
        return _ipv4_is_public(ip)
    if ip.scope_id:
        return False
    # IPv4 carried in IPv6 is judged by the IPv4 address alone (Python versions
    # disagree on is_global for these ranges). NAT64 answers come from DNS64 on
    # IPv6-only hosts.
    if ip.ipv4_mapped is not None:
        return _ipv4_is_public(ip.ipv4_mapped)
    if ip in _NAT64:
        return _ipv4_is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_site_local
        or ip.is_link_local
        or ip.is_loopback
        or ip.is_private
    ):
        return False
    embedded: list[ipaddress.IPv4Address] = []
    if ip.sixtofour is not None:
        embedded.append(ip.sixtofour)
    if ip.teredo is not None:
        embedded.extend(ip.teredo)
    return all(_ipv4_is_public(v4) for v4 in embedded)


def _host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    for entry in allowed_hosts:
        if entry.startswith("."):
            if host.endswith(entry) and len(host) > len(entry):
                return True
        elif host == entry:
            return True
    return False


def validate_endpoint_url(url: str, allowed_hosts: list[str]) -> str:
    """Check the endpoint's shape and host; return the (lower-case) host."""
    if len(url) > MAX_ENDPOINT_LENGTH or not url.isascii():
        raise UnsafeEndpoint("endpoint too long or not ASCII")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise UnsafeEndpoint("endpoint contains whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeEndpoint("endpoint must use https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeEndpoint("endpoint must not carry userinfo")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeEndpoint("endpoint port is invalid") from exc
    if port not in (None, 443):
        raise UnsafeEndpoint("endpoint must use the default https port")
    host = parts.hostname or ""
    if not host or host.endswith("."):
        raise UnsafeEndpoint("endpoint host is missing or not canonical")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UnsafeEndpoint("endpoint host must be a name, not an IP address")
    if not _host_allowed(host, allowed_hosts):
        raise UnsafeEndpoint("endpoint host is not an allowed push service")
    return host


async def _getaddrinfo(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def resolve_public(host: str) -> IPAddress:
    """Resolve ``host``; every address must be public. Returns the one to pin
    (IPv4 first, then the lowest, so retries are stable)."""
    try:
        raw = await asyncio.wait_for(_getaddrinfo(host), DNS_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        raise EndpointUnresolvable("endpoint host did not resolve") from exc
    addresses: set[IPAddress] = set()
    for value in raw:
        try:
            addresses.add(ipaddress.ip_address(value))
        except ValueError as exc:
            raise UnsafeEndpoint("endpoint host resolved to an unparsable address") from exc
    if not addresses:
        raise EndpointUnresolvable("endpoint host has no address")
    if not all(is_public_ip(a) for a in addresses):
        raise UnsafeEndpoint("endpoint host resolves to a non-public address")
    return min(addresses, key=lambda a: (a.version, int(a)))


async def check_endpoint(url: str, allowed_hosts: list[str]) -> tuple[str, IPAddress]:
    """Full check: shape, allowlist, then DNS. Returns (host, pinned address)."""
    host = validate_endpoint_url(url, allowed_hosts)
    return host, await resolve_public(host)


def pinned_url(url: str, ip: IPAddress) -> str:
    """``url`` with its host replaced by ``ip`` (path and query unchanged)."""
    parts = urlsplit(url)
    netloc = f"[{ip}]" if ip.version == 6 else str(ip)
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
