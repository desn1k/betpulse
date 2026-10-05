"""Static guards for outbound HTTP and saved provider responses.

1. Every outbound HTTP call in ``app/`` goes through ``app.core.outbound``
   (``outbound_client`` / ``check_status``), so its errors are scrubbed. Any other
   client — httpx without the wrapper (async or sync), ``raise_for_status``,
   requests, aiohttp, urllib3, urllib.request, http.client — fails here. The one
   documented exception is the OpenAI SDK in the LLM analysis module (key in a
   header, registered with the log safety net).
2. Nothing under ``tests/fixtures/`` (recorded provider responses, today and
   later) carries a key: neither a key-looking string nor the value of a secret
   from the environment or a local ``.env``. Failures name the file and the kind
   of match, never the value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
FIXTURES = BACKEND / "tests" / "fixtures"
REPO_ENV = BACKEND.parent / ".env"

OUTBOUND_MODULE = APP / "core" / "outbound.py"
OPENAI_ALLOWED = {APP / "services" / "llm" / "analysis.py"}

FORBIDDEN = {
    "httpx client outside outbound_client": re.compile(r"\bhttpx\s*\.\s*(Async)?Client\s*\("),
    "httpx client imported directly": re.compile(
        r"^\s*from\s+httpx\s+import\s+[^\n]*\b(Async)?Client\b", re.M
    ),
    "httpx top-level request function": re.compile(
        r"\bhttpx\s*\.\s*(get|post|put|patch|delete|head|options|request|stream)\s*\("
    ),
    "raise_for_status instead of check_status": re.compile(r"\.raise_for_status\s*\("),
    "requests / aiohttp / urllib3 / http.client / pycurl": re.compile(
        r"^\s*(import|from)\s+(requests|aiohttp|urllib3|http\.client|pycurl)\b", re.M
    ),
    "urllib.request": re.compile(
        r"\burllib\s*\.\s*request\b|\burlopen\s*\(|from\s+urllib\s+import\s+request"
    ),
}
OPENAI_CLIENT = re.compile(r"\b(Async)?OpenAI\s*\(")


def _app_sources() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_outbound_http_goes_through_the_scrubbing_client() -> None:
    offenders = []
    for path in _app_sources():
        if path == OUTBOUND_MODULE:
            continue
        source = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN.items():
            if pattern.search(source):
                offenders.append(f"{path.relative_to(BACKEND)}: {label}")
        if OPENAI_CLIENT.search(source) and path not in OPENAI_ALLOWED:
            offenders.append(
                f"{path.relative_to(BACKEND)}: OpenAI SDK client outside the LLM module"
            )
    assert not offenders, (
        "Outbound HTTP must use app.core.outbound.outbound_client / check_status:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "async with httpx.AsyncClient() as c: ...",
        "c = httpx.Client(timeout=1)",
        "from httpx import AsyncClient",
        "httpx.get('https://x')",
        "resp.raise_for_status()",
        "import requests",
        "from aiohttp import ClientSession",
        "import urllib.request",
        "urllib.request.urlopen('https://x')",
        "import http.client",
    ],
)
def test_guard_patterns_catch_each_client(snippet: str) -> None:
    assert any(p.search(snippet) for p in FORBIDDEN.values()), snippet


# --- fixtures -------------------------------------------------------------------

_SENSITIVE_NAMES = r"api[_-]?key|apikey|api[_-]?token|access[_-]?token|token|secret|password|key"
_PLACEHOLDER = re.compile(r"^(REDACTED|\*+|x+|<[^>]*>|test|dummy|example|changeme)$", re.I)

KEY_PATTERNS = {
    "telegram bot token": re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"),
    "secret query parameter": re.compile(rf"[?&](?:{_SENSITIVE_NAMES})=([^&\s\"'<>]+)", re.I),
    "secret JSON/YAML field": re.compile(
        rf"(?<![A-Za-z0-9_])[\"']?(?:{_SENSITIVE_NAMES}|authorization)[\"']?\s*[:=]\s*[\"']([^\"'\s]+)[\"']",
        re.I,
    ),
    "secret header": re.compile(
        r"(?im)^\s*(?:authorization|x-apisports-key|x-rapidapi-key|x-api-key|api-key|x-auth-token)"
        r"\s*:\s*(?:Bearer\s+)?(\S+)"
    ),
    "bearer token": re.compile(r"\bBearer\s+([A-Za-z0-9._~+/-]{16,}=*)"),
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
}


def find_key_like(text: str) -> list[str]:
    """Kinds of key-looking strings in ``text`` (values are never returned)."""
    found = []
    for kind, pattern in KEY_PATTERNS.items():
        for match in pattern.finditer(text):
            value = match.group(1) if match.groups() else match.group(0)
            if _PLACEHOLDER.match(value) or len(value) < 12:
                continue
            found.append(kind)
            break
    return found


_SECRET_ENV_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|DSN)", re.I)


def _known_secret_values() -> dict[str, str]:
    """name -> value for secrets in this environment and the repo-root .env
    (a developer's real provider keys live there)."""
    values: dict[str, str] = {}
    for name, value in os.environ.items():
        if _SECRET_ENV_NAME.search(name):
            values[f"env:{name}"] = value
    if REPO_ENV.is_file():
        for line in REPO_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.lstrip().startswith("#") and _SECRET_ENV_NAME.search(name):
                values[f".env:{name.strip()}"] = value.strip().strip("'\"")
    return {
        k: v
        for k, v in values.items()
        if len(v) >= 12 and not _PLACEHOLDER.match(v) and not v.startswith("#")
    }


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.rglob("*") if p.is_file())


# The Odds API names sports and bookmakers in a JSON field called "key"
# ("key": "soccer_epl"), which the "secret JSON/YAML field" pattern matches.
# Owner-approved exceptions (2026-10-05), per file, never global:
# - exact values in one odds file;
# - in the sports catalogue only, any lowercase snake_case identifier.
# Any other "key" value, in these files or any other, is still a finding.
KEY_FIELD_EXACT_VALUES: dict[str, frozenset[str]] = {
    "tests/fixtures/the_odds_api/free/odds_epl_h2h_eu.json": frozenset({"betfair_ex_eu"}),
}
KEY_FIELD_SNAKE_CASE_FILES = frozenset({"tests/fixtures/the_odds_api/free/sports.json"})
_KEY_FIELD = re.compile(r"\"key\"\s*:\s*\"([^\"]*)\"")
_SNAKE_CASE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def fixture_findings(relative_path: str, text: str) -> list[str]:
    """``find_key_like`` after blanking only the "key" fields allowed for this file."""
    exact = KEY_FIELD_EXACT_VALUES.get(relative_path, frozenset())
    snake = relative_path in KEY_FIELD_SNAKE_CASE_FILES

    def blank(match: re.Match[str]) -> str:
        value = match.group(1)
        if value in exact or (snake and _SNAKE_CASE.match(value)):
            return '"key": "REDACTED"'
        return match.group(0)

    return find_key_like(_KEY_FIELD.sub(blank, text))


def test_fixtures_carry_no_key_looking_strings() -> None:
    offenders = []
    for path in _fixture_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(BACKEND).as_posix()
        for kind in fixture_findings(relative, text):
            offenders.append(f"{relative}: {kind}")
    assert not offenders, "Sanitize these fixtures (replace keys with REDACTED):\n" + "\n".join(
        offenders
    )


def test_fixtures_carry_no_known_secret_value() -> None:
    known = _known_secret_values()
    offenders: list[str] = []
    for path in _fixture_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(
            f"{path.relative_to(BACKEND)}: value of {name}"
            for name, value in known.items()
            if value in text
        )
    assert not offenders, "A real secret is in a fixture:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("GET /v4/sports?apiKey=0123456789abcdef0123&regions=eu", "secret query parameter"),
        ('{"api_token": "AbCdEfGhIjKlMnOpQrSt"}', "secret JSON/YAML field"),
        ("x-apisports-key: 0123456789abcdef0123", "secret header"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "secret header"),
        (
            "https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/x",
            "telegram bot token",
        ),
        ("key sk-abcdefghijklmnopqrstuvwxyz012345", "openai-style key"),
    ],
)
def test_fixture_scanner_catches_planted_keys(text: str, kind: str) -> None:
    assert kind in find_key_like(text)


@pytest.mark.parametrize(
    "text",
    [
        "GET /v4/sports?apiKey=REDACTED&regions=eu",
        '{"api_token": "REDACTED", "token_type": "bearer"}',
        "Authorization: Bearer REDACTED",
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSD,PSA",
    ],
)
def test_fixture_scanner_accepts_sanitized_text(text: str) -> None:
    assert find_key_like(text) == []


_SPORTS = "tests/fixtures/the_odds_api/free/sports.json"
_ODDS = "tests/fixtures/the_odds_api/free/odds_epl_h2h_eu.json"


@pytest.mark.parametrize(
    ("path", "text"),
    [
        # Any other file: the exceptions do not apply.
        ("tests/fixtures/sportmonks/trial/leagues_p1.json", '{"key": "soccer_epl_identifier"}'),
        ("tests/fixtures/other/x.json", '{"key": "betfair_ex_eu"}'),
        # The sports catalogue: only snake_case identifiers pass.
        (_SPORTS, '{"key": "AbCdEf0123456789XyZ"}'),
        (_SPORTS, '{"key": "0f3a9c2b-1d4e-4f5a-9b8c-7d6e5f4a3b2c"}'),
        # The odds file: only the listed exact values pass.
        (_ODDS, '{"key": "unlisted_bookmaker_key"}'),
        # The exceptions cover the "key" field only, never the other patterns.
        (_SPORTS, '{"api_token": "AbCdEfGhIjKlMnOpQrSt"}'),
    ],
)
def test_key_field_exceptions_stay_narrow(path: str, text: str) -> None:
    assert "secret JSON/YAML field" in fixture_findings(path, text)


@pytest.mark.parametrize(
    ("path", "text"),
    [
        (_SPORTS, '{"key": "soccer_russia_premier_league", "group": "Soccer"}'),
        (_ODDS, '{"key": "betfair_ex_eu", "title": "Betfair"}'),
    ],
)
def test_key_field_exceptions_accept_the_approved_values(path: str, text: str) -> None:
    assert fixture_findings(path, text) == []
