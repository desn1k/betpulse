"""Shared security-header policy for API responses (Phase 13a)."""

from __future__ import annotations

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
