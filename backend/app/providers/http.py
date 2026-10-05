"""Minimal HTTP base for the paid data providers (Sportmonks, The Odds API).

Every call goes through :func:`app.core.outbound.outbound_client` with the key
registered as a secret, so it never reaches a log line or an exception. On top
of that this module adds only what each provider client needs:

- **Retries** for GET on 429 (honouring ``Retry-After``) and 5xx/transport
  errors, with exponential backoff and a bounded number of attempts. Other 4xx
  are never retried.
- **One log line per call**: provider, path (never the query, which may carry
  the key), status, duration and the provider's quota numbers — The Odds API
  credits (``x-requests-used/remaining/last``), Sportmonks' per-entity
  ``rate_limit`` block.

Adapters (mapping to DTOs) are separate PRs; these clients return the parsed
JSON and the quota.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from app.core.outbound import check_status, outbound_client

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# Never wait longer than this between two attempts, whatever the server asks.
MAX_RETRY_DELAY_SECONDS = 60.0


@dataclass(frozen=True)
class ProviderResponse:
    status: int
    data: Any
    quota: dict[str, Any] = field(default_factory=dict)


class ProviderHttpClient:
    """GET-only client for one provider. Subclasses set the name, base URL,
    how the key is sent and where the quota numbers live."""

    provider: ClassVar[str]
    base_url: ClassVar[str]

    def __init__(
        self,
        key: str,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not key:
            raise ValueError(f"{self.provider}: no API key configured")
        self._key = key
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleep

    # --- provider specifics -------------------------------------------------

    def auth_headers(self) -> dict[str, str]:
        return {}

    def auth_params(self) -> dict[str, str]:
        return {}

    def quota(self, response: httpx.Response, data: Any) -> dict[str, Any]:
        return {}

    def retry_after(self, response: httpx.Response, data: Any) -> float | None:
        raw = response.headers.get("retry-after")
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None

    # --- the call -------------------------------------------------------------

    async def get(
        self, path: str, params: Mapping[str, Any] | None = None, *, raise_on_error: bool = True
    ) -> ProviderResponse:
        """GET ``path``. With ``raise_on_error=False`` a final 4xx/5xx is
        returned (used to record error bodies) instead of raised."""
        query = {**(params or {}), **self.auth_params()}
        async with outbound_client(
            secrets=[self._key],
            base_url=self.base_url,
            headers=self.auth_headers(),
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            for attempt in range(1, self._max_attempts + 1):
                last = attempt == self._max_attempts
                started = time.monotonic()
                try:
                    response = await client.get(path, params=query)
                except httpx.TransportError as exc:
                    logger.warning(
                        "%s GET %s: %s (attempt %d/%d)",
                        self.provider,
                        path,
                        type(exc).__name__,
                        attempt,
                        self._max_attempts,
                    )
                    if last:
                        raise
                    await self._sleep(self._backoff * 2 ** (attempt - 1))
                    continue

                data = _json_or_none(response)
                quota = self.quota(response, data)
                logger.info(
                    "%s GET %s -> %d in %d ms (attempt %d/%d) quota %s",
                    self.provider,
                    path,
                    response.status_code,
                    round((time.monotonic() - started) * 1000),
                    attempt,
                    self._max_attempts,
                    quota,
                )
                if response.status_code in RETRY_STATUSES and not last:
                    delay = self.retry_after(response, data)
                    if delay is None:
                        delay = self._backoff * 2 ** (attempt - 1)
                    await self._sleep(min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS))
                    continue
                if raise_on_error:
                    check_status(response)
                return ProviderResponse(status=response.status_code, data=data, quota=quota)
        raise AssertionError("unreachable")  # pragma: no cover


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _int_header(response: httpx.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    try:
        return int(float(raw)) if raw is not None else None
    except ValueError:
        return None


class TheOddsApiClient(ProviderHttpClient):
    """The Odds API v4. The key travels only as the ``apiKey`` query parameter
    (no header option); every response carries the credit headers."""

    provider = "the_odds_api"
    base_url = "https://api.the-odds-api.com"

    def auth_params(self) -> dict[str, str]:
        return {"apiKey": self._key}

    def quota(self, response: httpx.Response, data: Any) -> dict[str, Any]:
        return {
            "credits_last": _int_header(response, "x-requests-last"),
            "credits_used": _int_header(response, "x-requests-used"),
            "credits_remaining": _int_header(response, "x-requests-remaining"),
        }


class SportmonksClient(ProviderHttpClient):
    """Sportmonks v3. The token goes in the ``Authorization`` header; limits are
    per entity per hour and reported in the body's ``rate_limit`` block."""

    provider = "sportmonks"
    base_url = "https://api.sportmonks.com"

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": self._key}

    def quota(self, response: httpx.Response, data: Any) -> dict[str, Any]:
        block = data.get("rate_limit") if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return {}
        return {
            "remaining": block.get("remaining"),
            "resets_in_seconds": block.get("resets_in_seconds"),
            "entity": block.get("requested_entity"),
        }

    def retry_after(self, response: httpx.Response, data: Any) -> float | None:
        header = super().retry_after(response, data)
        if header is not None:
            return header
        block = data.get("rate_limit") if isinstance(data, dict) else None
        reset = block.get("resets_in_seconds") if isinstance(block, dict) else None
        return float(reset) if isinstance(reset, int | float) else None
