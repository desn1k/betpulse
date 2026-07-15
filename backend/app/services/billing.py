"""Billing seam (spec §7).

The subscription model already carries ``source = payment``; this is the
interface an online-payment provider (YooKassa / Stripe / crypto) will implement
later so it plugs in without a schema change. **No implementation yet** — the
abstract surface is defined now so the rest of the system can depend on it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckoutSession:
    """A provider-hosted checkout the user is redirected to."""

    url: str
    external_id: str


class PaymentProvider(abc.ABC):
    """Contract for an online-payment provider. Implementations are added in a
    later phase; promo codes (Phase 8) and manual grants cover monetization now."""

    @abc.abstractmethod
    async def create_checkout(self, *, user_id: str, tier_name: str) -> CheckoutSession:
        """Start a checkout for ``tier_name`` and return where to send the user."""

    @abc.abstractmethod
    async def handle_webhook(self, *, payload: bytes, signature: str) -> None:
        """Verify and apply a provider webhook (grant/renew/cancel a subscription)."""
