"""Contract tests on real provider responses (recorded 2026-10-05).

They pin the fields the planned adapters (provider PRs 2 and 4) will read, so a
re-recorded response with a different shape fails here first. The fixtures are
written by ``python -m app.cli provider-record`` (see app/providers/recording.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.providers.http import SportmonksClient, TheOddsApiClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ODDS = FIXTURES / "the_odds_api" / "free"
SM = FIXTURES / "sportmonks" / "trial"


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# --- The Odds API -----------------------------------------------------------------


def test_odds_h2h_shape() -> None:
    rec = _load(ODDS / "odds_epl_h2h_eu.json")
    assert rec["status"] == 200
    assert set(rec["quota"]) == {"credits_last", "credits_used", "credits_remaining"}
    assert rec["quota"]["credits_last"] == 1  # 1 market x 1 region
    events = rec["body"]
    assert events
    for event in events:
        assert {"id", "commence_time", "home_team", "away_team", "bookmakers"} <= set(event)
        assert event["commence_time"].endswith("Z")
        for bookmaker in event["bookmakers"]:
            assert {"key", "title", "last_update", "markets"} <= set(bookmaker)
            # Exchanges (betfair_ex_eu, matchbook) also return h2h_lay.
            by_key = {m["key"]: m for m in bookmaker["markets"]}
            assert set(by_key) <= {"h2h", "h2h_lay"}
            market = by_key["h2h"]
            names = {o["name"] for o in market["outcomes"]}
            assert names == {event["home_team"], event["away_team"], "Draw"}
            assert all(o["price"] > 1.0 for o in market["outcomes"])


def test_odds_totals_carry_a_point() -> None:
    events = _load(ODDS / "odds_epl_totals_eu.json")["body"]
    outcomes = [
        o for e in events for b in e["bookmakers"] for m in b["markets"] for o in m["outcomes"]
    ]
    assert outcomes and all(o["name"] in {"Over", "Under"} and "point" in o for o in outcomes)


@pytest.mark.parametrize("name", ["historical_odds_epl", "historical_events_epl"])
def test_odds_history_is_refused_on_the_free_key(name: str) -> None:
    rec = _load(ODDS / f"{name}.json")
    assert rec["status"] == 401
    assert rec["body"]["error_code"] == "HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN"
    assert rec["quota"]["credits_last"] == 0


def test_odds_error_bodies_carry_an_error_code() -> None:
    assert _load(ODDS / "error_invalid_key.json")["body"]["error_code"] == "INVALID_KEY"
    assert _load(ODDS / "error_bad_market.json")["body"]["error_code"] == "INVALID_MARKET"


@pytest.mark.asyncio
async def test_odds_client_reads_the_recorded_quota() -> None:
    rec = _load(ODDS / "odds_epl_h2h_eu.json")
    headers = {
        "x-requests-last": str(rec["quota"]["credits_last"]),
        "x-requests-used": str(rec["quota"]["credits_used"]),
        "x-requests-remaining": str(rec["quota"]["credits_remaining"]),
    }
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json=rec["body"], headers=headers)
    )
    resp = await TheOddsApiClient("k" * 20, transport=transport).get("/v4/sports/soccer_epl/odds")
    assert resp.quota == rec["quota"]


# --- Sportmonks ---------------------------------------------------------------------


def test_sportmonks_leagues_cover_our_six() -> None:
    body = _load(SM / "leagues_p1.json")["body"]
    ids = {league["id"] for league in body["data"]}
    assert {8, 564, 384, 82, 301, 486} <= ids
    assert {"count", "per_page", "has_more"} <= set(body["pagination"])
    assert {"remaining", "resets_in_seconds", "requested_entity"} <= set(body["rate_limit"])


def test_sportmonks_fixture_shape_with_includes() -> None:
    body = _load(SM / "b_fixtures_2024_epl_opening_full.json")["body"]
    assert body["data"]
    for fx in body["data"]:
        assert isinstance(fx["starting_at_timestamp"], int)
        assert fx["state"]["developer_name"] == "FT"
        locations = {p["meta"]["location"] for p in fx["participants"]}
        assert locations == {"home", "away"}
        descriptions = {s["description"] for s in fx["scores"]}
        assert {"1ST_HALF", "2ND_HALF", "CURRENT"} <= descriptions
        assert all(s["score"]["participant"] in {"home", "away"} for s in fx["scores"])
        stat_types = {s["type"]["developer_name"] for s in fx["statistics"]}
        assert {"SHOTS_TOTAL", "SHOTS_ON_TARGET", "CORNERS", "BALL_POSSESSION"} <= stat_types
        assert "EXPECTED_GOALS" not in stat_types  # no xG on this plan
        assert fx["lineups"]


def test_sportmonks_empty_and_missing_answer_200_with_a_message() -> None:
    for name in ("error_not_found", "fixtures_2019_epl"):
        rec = _load(SM / f"{name}.json")
        assert rec["status"] == 200
        assert rec["body"]["message"].startswith("No result(s) found")


@pytest.mark.parametrize("name", ["fixtures_recent_xg_epl", "error_premium_include"])
def test_sportmonks_refused_includes_are_403(name: str) -> None:
    rec = _load(SM / f"{name}.json")
    assert rec["status"] == 403
    assert rec["body"]["code"] == 5002


@pytest.mark.asyncio
async def test_sportmonks_client_reads_the_recorded_rate_limit() -> None:
    body = _load(SM / "leagues_p1.json")["body"]
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    resp = await SportmonksClient("k" * 20, transport=transport).get("/v3/football/leagues")
    assert resp.quota["entity"] == "League"
