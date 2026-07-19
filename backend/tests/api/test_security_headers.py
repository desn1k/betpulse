"""Phase 13a security-header baseline."""

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


async def test_security_headers_do_not_break_cors_preflight(client: AsyncClient) -> None:
    resp = await client.options(
        "/health",
        headers={
            "Access-Control-Request-Method": "GET",
            "Origin": "http://localhost:3000",
        },
    )

    assert resp.status_code in {200, 400}
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert resp.headers["x-content-type-options"] == "nosniff"
