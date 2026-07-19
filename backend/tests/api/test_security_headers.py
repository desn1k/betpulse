"""Phase 13 browser and API response security controls."""

from __future__ import annotations

from httpx import AsyncClient


async def test_security_headers_are_added_to_api_responses(client: AsyncClient) -> None:
    resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert resp.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


async def test_allowed_origin_and_explicit_headers_pass_cors_preflight(
    client: AsyncClient,
) -> None:
    resp = await client.options(
        "/health",
        headers={
            "Access-Control-Request-Headers": "authorization,content-type,x-csrf-token",
            "Access-Control-Request-Method": "POST",
            "Origin": "http://localhost:3000",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "POST" in resp.headers["access-control-allow-methods"]
    allowed_headers = resp.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "x-csrf-token" in allowed_headers
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_untrusted_origin_is_rejected_by_cors_preflight(client: AsyncClient) -> None:
    resp = await client.options(
        "/health",
        headers={
            "Access-Control-Request-Method": "GET",
            "Origin": "https://attacker.example",
        },
    )

    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
