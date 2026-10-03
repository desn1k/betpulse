"""Which bookmaker quote to use — an explicit time rule, never row order.

The ``odds`` table is a time series: a fixture can carry several quotes per
outcome (opening, intermediate, closing, in-play). Every consumer picks one by
time:

* :func:`quote_at` — the latest **complete** quote snapshot (every outcome of
  the market quoted at the same ``ts``) with ``ts <= as_of``. Mixing outcomes
  from different moments would build a price set no bookmaker ever offered.
* the **closing** quote is ``quote_at(..., kickoff)`` over rows flagged
  ``is_closing`` only. The bound is inclusive because closing odds are stored
  with ``ts = kickoff``; anything later is in-play and never used for pre-match
  work. A **pre-closing** quote is never a closing one: a fixture that has only
  pre-closing quotes has *no* closing quote (it is left out of "ROI vs
  closing", the market model and the backtester), rather than being scored on
  a stale price;
* the **reference bookmaker** depends on the kickoff date
  (``settings.reference_bookmakers``): Pinnacle before 2025-07-23, the market
  average closing price (``market_avg``) from that date on (inclusive).
* a simulated bet uses ``quote_at(..., decision_time)``. The backtester decides
  at kickoff, i.e. it settles at the closing quote (``odds_basis = closing``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.fixture import Fixture
from app.models.market import Odds

MARKET_OUTCOMES: dict[str, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "ou_2.5": ("over", "under"),
}


def reference_bookmaker_for(kickoff: datetime) -> str:
    """The configured reference bookmaker for a match kicking off at ``kickoff``
    (by UTC date; ranges are ``[from_date, until_date)``)."""
    day = kickoff.astimezone(UTC).date()
    for r in get_settings().reference_bookmakers:
        if (r.from_date is None or day >= r.from_date) and (
            r.until_date is None or day < r.until_date
        ):
            return r.bookmaker
    raise LookupError(f"no reference bookmaker configured for {day}")  # pragma: no cover


def quote_at(rows: Iterable[Odds], *, market: str, as_of: datetime) -> dict[str, Decimal] | None:
    """Latest complete ``market`` snapshot quoted at or before ``as_of``, as
    ``{outcome: price}``; ``None`` when there is none. Deterministic regardless
    of row order (ties on ``ts`` cannot happen: ``ts`` is part of the key)."""
    outcomes = MARKET_OUTCOMES[market]
    by_ts: dict[datetime, dict[str, Decimal]] = defaultdict(dict)
    for row in rows:
        if row.market == market and row.ts <= as_of and row.outcome in outcomes:
            by_ts[row.ts][row.outcome] = row.price
    for ts in sorted(by_ts, reverse=True):
        if set(by_ts[ts]) == set(outcomes):
            return dict(by_ts[ts])
    return None


async def closing_quotes(
    session: AsyncSession,
    fixtures: Iterable[Fixture],
    *,
    markets: tuple[str, ...] = ("1x2",),
    bookmaker: str | None = None,
) -> dict[uuid.UUID, dict[str, Decimal]]:
    """Closing quote per fixture (``is_closing`` rows with ``ts <= kickoff``) of
    ``bookmaker`` — by default each fixture's date-based reference bookmaker —
    markets merged into one ``{outcome: price}`` dict (1X2 and O/U outcome names
    never collide)."""
    fixtures = list(fixtures)
    kickoff = {fx.id: fx.kickoff_at for fx in fixtures}
    if not kickoff:
        return {}
    wanted = {fx.id: bookmaker or reference_bookmaker_for(fx.kickoff_at) for fx in fixtures}
    rows = (
        (
            await session.execute(
                select(Odds).where(
                    Odds.fixture_id.in_(list(kickoff)),
                    Odds.bookmaker.in_(set(wanted.values())),
                    Odds.market.in_(markets),
                    Odds.is_closing.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    by_fixture: dict[uuid.UUID, list[Odds]] = defaultdict(list)
    for row in rows:
        if row.bookmaker == wanted[row.fixture_id]:
            by_fixture[row.fixture_id].append(row)

    out: dict[uuid.UUID, dict[str, Decimal]] = {}
    for fixture_id, fixture_rows in by_fixture.items():
        merged: dict[str, Decimal] = {}
        for market in markets:
            quote = quote_at(fixture_rows, market=market, as_of=kickoff[fixture_id])
            if quote is not None:
                merged.update(quote)
        if merged:
            out[fixture_id] = merged
    return out
