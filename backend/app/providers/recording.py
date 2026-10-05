"""Record real provider responses as sanitised test fixtures (dev tool).

``python -m app.cli provider-record --provider P --manifest FILE [--execute]``

- The **manifest** (JSON, committed next to the fixtures) lists the calls: a
  name, a path, query parameters, the expected credit cost and, for error
  samples, ``"auth": "invalid"`` (a deliberately wrong key), and the statuses
  it may answer (``expect``, default ``[200]``). Any other status stops the run.
- **Dry run by default**: it prints each call and the expected credits and makes
  no request. ``--execute`` makes them, one by one, and prints the quota after
  each call. It stops before a call whose expected credits would exceed
  ``--max-credits`` (default: the manifest's total) or the credits the provider
  reports as remaining.
- **Keys** come only from the env file (``--env-file``, default ``.env``); they
  are registered with the scrubber and never printed.
- **Sanitising**: account blocks (``subscription``, ``plans``) are dropped, and
  the provider key is replaced with ``REDACTED`` before a file is written (not
  every registered secret: see :func:`sanitize`). ``tests/test_outbound_guard.py``
  scans the result again for key-like strings and for the env file's secret
  values.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.outbound import REDACTED, redact_text, register_secret
from app.providers.http import ProviderHttpClient, SportmonksClient, TheOddsApiClient

# A wrong key used on purpose to record the provider's 401 body.
INVALID_KEY = "invalid-key-for-error-sample"
# Response fields that describe our account, not football data.
ACCOUNT_FIELDS = frozenset({"subscription", "subscriptions", "plan", "plans"})

CLIENTS: dict[str, type[ProviderHttpClient]] = {
    TheOddsApiClient.provider: TheOddsApiClient,
    SportmonksClient.provider: SportmonksClient,
}


class ProviderKeys(BaseSettings):
    """Only the provider keys, read from the env file (nothing else is needed
    or validated, so a production-style .env does not get in the way)."""

    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    sportmonks_api_token: str = ""
    the_odds_api_key: str = ""

    def for_provider(self, provider: str) -> str:
        return {
            SportmonksClient.provider: self.sportmonks_api_token,
            TheOddsApiClient.provider: self.the_odds_api_key,
        }[provider]


@dataclass(frozen=True)
class Call:
    name: str
    path: str
    params: dict[str, Any]
    credits: int = 0
    invalid_key: bool = False
    expect: tuple[int, ...] = (200,)


def load_manifest(path: Path) -> list[Call]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    calls = [
        Call(
            name=item["name"],
            path=item["path"],
            params=dict(item.get("params", {})),
            credits=int(item.get("credits", 0)),
            invalid_key=item.get("auth") == "invalid",
            expect=tuple(item.get("expect", [200])),
        )
        for item in raw["calls"]
    ]
    names = [c.name for c in calls]
    if len(names) != len(set(names)):
        raise ValueError("manifest call names must be unique")
    return calls


def _redact_key(text: str, key: str) -> str:
    for form in sorted({key, quote(key, safe="")}, key=len, reverse=True):
        text = text.replace(form, REDACTED)
    return text


def sanitize(value: Any, key: str) -> Any:
    """Drop account fields (recursively) and replace the provider key with
    ``REDACTED``. Only the key: the log scrubber's registry also holds weak dev
    values (the default DB password ``football``) that would rewrite real data
    (``americanfootball_nfl``, ``/v3/football``). The fixture guard scans the
    result for any other secret."""
    if isinstance(value, dict):
        return {k: sanitize(v, key) for k, v in value.items() if k not in ACCOUNT_FIELDS}
    if isinstance(value, list):
        return [sanitize(v, key) for v in value]
    if isinstance(value, str):
        return _redact_key(value, key)
    return value


def plan_text(provider: str, calls: list[Call]) -> str:
    lines = [f"{provider}: {len(calls)} calls, expected credits {sum(c.credits for c in calls)}"]
    for call in calls:
        auth = " [invalid key]" if call.invalid_key else ""
        lines.append(f"  {call.name}: GET {call.path} {call.params} credits={call.credits}{auth}")
    return "\n".join(lines)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def record(
    provider: str,
    calls: list[Call],
    *,
    key: str,
    out_dir: Path,
    max_credits: int,
    client_kwargs: dict[str, Any] | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[int, bool]:
    """Make the calls and write one fixture per call. Returns the credits the
    provider reported as spent (``credits_last`` summed; 0 when it reports none)
    and whether every call ran (False when the run stopped early)."""
    if not key:
        raise ValueError(f"{provider}: no key in the env file")
    register_secret(key)
    client_cls = CLIENTS[provider]
    spent = 0
    completed = True
    remaining: int | None = None
    for call in calls:
        if spent + call.credits > max_credits:
            echo(f"STOP before {call.name}: {spent} + {call.credits} > max {max_credits}")
            completed = False
            break
        if remaining is not None and call.credits > remaining:
            echo(f"STOP before {call.name}: needs {call.credits}, {remaining} remaining")
            completed = False
            break
        client = client_cls(INVALID_KEY if call.invalid_key else key, **(client_kwargs or {}))
        try:
            response = await client.get(call.path, call.params, raise_on_error=False)
        except httpx.HTTPError as exc:
            echo(f"STOP at {call.name}: {type(exc).__name__}: {redact_text(str(exc))}")
            completed = False
            break
        last = response.quota.get("credits_last")
        spent += last if isinstance(last, int) else 0
        if isinstance(response.quota.get("credits_remaining"), int):
            remaining = response.quota["credits_remaining"]
        fixture = {
            "request": {"method": "GET", "path": call.path, "params": call.params},
            "auth": "invalid" if call.invalid_key else "valid",
            "status": response.status,
            "quota": response.quota,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "body": sanitize(response.data, key),
        }
        text = _redact_key(json.dumps(fixture, ensure_ascii=False, indent=2), key) + "\n"
        if key in text:  # belt and braces: sanitising must have caught it
            raise RuntimeError(f"{call.name}: key survived sanitising; nothing written")
        _write(out_dir / f"{call.name}.json", text)
        echo(f"{call.name}: {response.status} quota {response.quota}")
        if response.status not in call.expect:
            echo(f"STOP after {call.name}: status {response.status}, expected {list(call.expect)}")
            completed = False
            break
    echo(f"{provider}: credits spent {spent}" + ("" if completed else " (stopped early)"))
    return spent, completed
