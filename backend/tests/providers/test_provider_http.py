"""The paid-provider HTTP base: key placement, retries, quota logging, no key leaks."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.providers.http import SportmonksClient, TheOddsApiClient
from app.providers.recording import INVALID_KEY, Call, load_manifest, record, sanitize

ODDS_KEY = "odds-test-key-7f3a9c2b1d4e"
SM_KEY = "sportmonks-test-token-9e8d7c6b5a4f"


def _transport(responses: list[httpx.Response], seen: list[httpx.Request]) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler)


def _odds_headers(last: int, used: int, remaining: int) -> dict[str, str]:
    return {
        "x-requests-last": str(last),
        "x-requests-used": str(used),
        "x-requests-remaining": str(remaining),
    }


@pytest.fixture(autouse=True)
def _http_logger_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alembic's fileConfig (the migration tests) disables loggers that exist at
    that point; caplog must still see this module's lines when run after them."""
    monkeypatch.setattr(logging.getLogger("app.providers.http"), "disabled", False)


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _odds(responses: list[httpx.Response], seen: list[httpx.Request], **kw: Any) -> Any:
    sleeps = _Sleeps()
    client = TheOddsApiClient(ODDS_KEY, transport=_transport(responses, seen), sleep=sleeps, **kw)
    return client, sleeps


@pytest.mark.asyncio
async def test_odds_api_sends_the_key_as_a_query_param_and_logs_credits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[httpx.Request] = []
    client, _ = _odds([httpx.Response(200, json=[], headers=_odds_headers(1, 11, 489))], seen)
    with caplog.at_level(logging.INFO, logger="app.providers.http"):
        resp = await client.get("/v4/sports/soccer_epl/odds", {"regions": "eu"})
    assert seen[0].url.params["apiKey"] == ODDS_KEY
    assert "authorization" not in seen[0].headers
    assert resp.quota == {"credits_last": 1, "credits_used": 11, "credits_remaining": 489}
    text = caplog.text
    assert "the_odds_api GET /v4/sports/soccer_epl/odds -> 200" in text
    assert "'credits_used': 11" in text and "'credits_remaining': 489" in text
    assert ODDS_KEY not in text


@pytest.mark.asyncio
async def test_sportmonks_sends_the_token_as_a_header_and_reads_rate_limit() -> None:
    seen: list[httpx.Request] = []
    body = {
        "data": [],
        "rate_limit": {"resets_in_seconds": 3000, "remaining": 2999, "requested_entity": "League"},
    }
    client = SportmonksClient(SM_KEY, transport=_transport([httpx.Response(200, json=body)], seen))
    resp = await client.get("/v3/football/leagues")
    assert seen[0].headers["authorization"] == SM_KEY
    assert "api_token" not in seen[0].url.params
    assert resp.quota == {"remaining": 2999, "resets_in_seconds": 3000, "entity": "League"}


@pytest.mark.asyncio
async def test_429_is_retried_after_the_server_delay() -> None:
    seen: list[httpx.Request] = []
    client, sleeps = _odds(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json={"ok": True}),
        ],
        seen,
    )
    resp = await client.get("/v4/sports")
    assert resp.status == 200 and len(seen) == 2
    assert sleeps.calls == [7.0]


@pytest.mark.asyncio
async def test_sportmonks_429_waits_for_the_rate_limit_reset() -> None:
    seen: list[httpx.Request] = []
    sleeps = _Sleeps()
    client = SportmonksClient(
        SM_KEY,
        sleep=sleeps,
        transport=_transport(
            [
                httpx.Response(429, json={"rate_limit": {"resets_in_seconds": 12}}),
                httpx.Response(200, json={"data": []}),
            ],
            seen,
        ),
    )
    await client.get("/v3/football/leagues")
    assert sleeps.calls == [12.0]


@pytest.mark.asyncio
async def test_5xx_retries_are_bounded_and_the_error_carries_no_key() -> None:
    seen: list[httpx.Request] = []
    client, sleeps = _odds([httpx.Response(503)] * 3, seen, backoff_seconds=0.5)
    with pytest.raises(httpx.HTTPStatusError) as info:
        await client.get("/v4/sports")
    assert len(seen) == 3
    assert sleeps.calls == [0.5, 1.0]
    assert ODDS_KEY not in str(info.value)
    assert ODDS_KEY not in str(info.value.request.url)


@pytest.mark.asyncio
async def test_other_4xx_are_not_retried() -> None:
    seen: list[httpx.Request] = []
    client, sleeps = _odds([httpx.Response(401, json={"message": "bad key"})], seen)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/v4/sports")
    assert len(seen) == 1 and sleeps.calls == []


@pytest.mark.asyncio
async def test_an_error_can_be_returned_for_recording() -> None:
    seen: list[httpx.Request] = []
    client, _ = _odds([httpx.Response(401, json={"message": "bad key"})], seen)
    resp = await client.get("/v4/sports", raise_on_error=False)
    assert resp.status == 401 and resp.data == {"message": "bad key"}


@pytest.mark.asyncio
async def test_transport_errors_are_retried_then_raised_without_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    sleeps = _Sleeps()
    client = TheOddsApiClient(ODDS_KEY, transport=httpx.MockTransport(handler), sleep=sleeps)
    with caplog.at_level(logging.WARNING, logger="app.providers.http"):
        with pytest.raises(httpx.ConnectError) as info:
            await client.get("/v4/sports")
    assert attempts == 3 and len(sleeps.calls) == 2
    assert ODDS_KEY not in str(info.value)
    assert ODDS_KEY not in caplog.text


def test_a_missing_key_fails_before_any_call() -> None:
    with pytest.raises(ValueError, match="no API key"):
        TheOddsApiClient("")


# --- recording -----------------------------------------------------------------


def test_sanitize_drops_account_blocks_and_redacts_the_key() -> None:
    body = {
        "data": [{"name": "Premier League", "url": f"https://x/?t={SM_KEY}"}],
        "subscription": [{"plans": [{"plan": "Growth"}]}],
    }
    clean = sanitize(body, SM_KEY)
    assert "subscription" not in clean
    assert SM_KEY not in json.dumps(clean)
    assert clean["data"][0]["url"] == "https://x/?t=REDACTED"
    assert clean["data"][0]["name"] == "Premier League"


def test_sanitize_leaves_other_registered_secrets_alone() -> None:
    """A weak dev secret (the default DB password ``football``) is registered
    with the log scrubber; it must not rewrite real data such as
    ``americanfootball_nfl`` or ``/v3/football/leagues``."""
    from app.core.outbound import register_secret

    register_secret("football")
    body = {"key": "americanfootball_nfl", "path": "/v3/football/leagues"}
    assert sanitize(body, SM_KEY) == body


def test_manifest_names_must_be_unique(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"calls": [{"name": "a", "path": "/x"}] * 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_manifest(path)


def _record_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    used = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal used
        seen.append(request)
        if request.url.params.get("apiKey") == INVALID_KEY:
            return httpx.Response(401, json={"message": "Invalid key"})
        cost = 1 if request.url.path.endswith("/odds") else 0
        used += cost
        return httpx.Response(
            200,
            json=[{"key": "soccer_epl", "echo": str(request.url)}],
            headers=_odds_headers(cost, used, 500 - used),
        )

    return httpx.MockTransport(handler)


def _check_recorded_files(out: Path) -> None:
    assert sorted(p.name for p in out.iterdir()) == [
        "error_401.json",
        "odds_h2h.json",
        "sports.json",
    ]
    for path in out.iterdir():
        assert ODDS_KEY not in path.read_text(encoding="utf-8")
    odds = json.loads((out / "odds_h2h.json").read_text(encoding="utf-8"))
    assert odds["status"] == 200 and odds["quota"]["credits_last"] == 1
    assert odds["request"] == {
        "method": "GET",
        "path": "/v4/sports/soccer_epl/odds",
        "params": {"regions": "eu"},
    }
    assert json.loads((out / "error_401.json").read_text(encoding="utf-8"))["status"] == 401


@pytest.mark.asyncio
async def test_record_writes_sanitised_fixtures_and_counts_credits(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    lines: list[str] = []
    calls = [
        Call("sports", "/v4/sports", {}),
        Call("odds_h2h", "/v4/sports/soccer_epl/odds", {"regions": "eu"}, credits=1),
        Call("error_401", "/v4/sports", {}, invalid_key=True, expect=(401,)),
    ]
    spent, completed = await record(
        "the_odds_api",
        calls,
        key=ODDS_KEY,
        out_dir=tmp_path,
        max_credits=5,
        client_kwargs={"transport": _record_transport(seen)},
        echo=lines.append,
    )
    assert spent == 1 and completed
    _check_recorded_files(tmp_path)
    assert all(ODDS_KEY not in line for line in lines)
    assert lines[-1] == "the_odds_api: credits spent 1"


@pytest.mark.asyncio
async def test_record_stops_before_exceeding_the_credit_cap(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    lines: list[str] = []
    calls = [
        Call("odds_a", "/v4/sports/soccer_epl/odds", {}, credits=1),
        Call("odds_b", "/v4/sports/soccer_epl/odds", {}, credits=1),
    ]
    spent, completed = await record(
        "the_odds_api",
        calls,
        key=ODDS_KEY,
        out_dir=tmp_path,
        max_credits=1,
        client_kwargs={"transport": _record_transport(seen)},
        echo=lines.append,
    )
    assert spent == 1 and len(seen) == 1 and not completed
    assert any(line.startswith("STOP before odds_b") for line in lines)


def test_cli_dry_run_makes_no_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import cli

    def boom(*args: Any, **kwargs: Any) -> Callable[..., Any]:
        raise AssertionError("no client in a dry run")

    monkeypatch.setattr("app.providers.recording.record", boom)
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"calls": [{"name": "odds", "path": "/v4/x", "credits": 2}]}), encoding="utf-8"
    )
    argv = ["provider-record", "--provider", "the_odds_api", "--manifest", str(manifest)]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "expected credits 2" in out and "dry run" in out


@pytest.mark.asyncio
async def test_record_stops_on_an_unexpected_status(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    lines: list[str] = []
    calls = [
        Call("error_401", "/v4/sports", {}, invalid_key=True),  # expects 200 by default
        Call("sports", "/v4/sports", {}),
    ]
    _, completed = await record(
        "the_odds_api",
        calls,
        key=ODDS_KEY,
        out_dir=tmp_path,
        max_credits=0,
        client_kwargs={"transport": _record_transport(seen)},
        echo=lines.append,
    )
    assert len(seen) == 1
    assert "STOP after error_401: status 401, expected [200]" in lines
    assert not completed


@pytest.mark.parametrize(("completed", "code"), [(True, 0), (False, 1)])
def test_cli_exit_code_reports_an_incomplete_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed: bool, code: int
) -> None:
    from app import cli

    async def fake_record(*args: Any, **kwargs: Any) -> tuple[int, bool]:
        return 0, completed

    monkeypatch.setattr("app.providers.recording.record", fake_record)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"calls": [{"name": "a", "path": "/x"}]}), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("THE_ODDS_API_KEY=k" + chr(10), encoding="utf-8")
    argv = ["provider-record", "--provider", "the_odds_api", "--manifest", str(manifest)]
    assert cli.main([*argv, "--env-file", str(env), "--execute"]) == code
