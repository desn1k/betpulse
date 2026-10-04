"""Outbound HTTP scrubber (app/core/outbound.py): no key, token or password
ever shows up in an exception (text, repr, traceback with its chain) or in a log
line, for every outbound client and through the log-record safety net."""

from __future__ import annotations

import io
import logging
import secrets
import traceback
from collections.abc import Iterator

import httpx
import openai
import pytest
import respx
from app.core.config import get_settings
from app.core.outbound import (
    QUIET_LOGGERS,
    REDACTED,
    check_status,
    install_log_safety,
    outbound_client,
    redact_headers,
    redact_text,
    redact_url,
    register_secret,
)
from app.providers.api_football import ApiFootballProvider
from app.services.live.push import PushError, send_telegram


def _telegram_token() -> str:
    return f"{secrets.randbelow(10**9) + 10**8}:AA{secrets.token_urlsafe(30)}"


def _rendered(exc: BaseException) -> str:
    """Everything a log line or an error tracker could show about ``exc``."""
    return "\n".join(
        [str(exc), repr(exc), "".join(traceback.format_exception(exc))]
        + ([str(exc.request.url)] if isinstance(exc, httpx.HTTPError) and _has_request(exc) else [])
    )


def _has_request(exc: httpx.HTTPError) -> bool:
    try:
        exc.request  # noqa: B018
    except RuntimeError:
        return False
    return True


def _assert_absent(secret: str, *texts: str) -> None:
    for text in texts:
        assert secret not in text


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    """A real handler on the root logger, formatting like production (with
    tracebacks), after the safety net is installed."""
    install_log_safety()
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)


# --- redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://api.the-odds-api.com/v4/sports/soccer_epl/odds?apiKey=abc123secret&regions=eu",
            f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds?apiKey={REDACTED}&regions=eu",
        ),
        (
            "https://api.sportmonks.com/v3/football/fixtures?api_token=tok123456789&include=scores",
            f"https://api.sportmonks.com/v3/football/fixtures?api_token={REDACTED}&include=scores",
        ),
        (
            "https://x.test/a?APIKEY=1&Key=2&token=3",
            f"https://x.test/a?APIKEY={REDACTED}&Key={REDACTED}&token={REDACTED}",
        ),
        ("https://x.test/a?api%5Fkey=1", f"https://x.test/a?api%5Fkey={REDACTED}"),
        (
            "https://x.test/a?apiKey=1&apiKey=2",
            f"https://x.test/a?apiKey={REDACTED}&apiKey={REDACTED}",
        ),
        ("https://user:p4ss@db.test:5432/x", "https://***@db.test:5432/x"),
        (
            "postgresql+asyncpg://football:hunter2pass@postgres:5432/football",
            "postgresql+asyncpg://***@postgres:5432/football",
        ),
        (
            "https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage",
            "https://api.telegram.org/bot***/sendMessage",
        ),
        (
            "https://fcm.googleapis.com/fcm/send/abc?x=1",
            "https://fcm.googleapis.com/fcm/send/abc?x=1",
        ),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected


def test_redact_text_finds_urls_tokens_and_registered_secrets() -> None:
    token = _telegram_token()
    secret = "s3cr3t/value+with=chars"
    register_secret(secret)
    encoded = httpx.URL("https://a.test/", params={"q": secret}).query.decode()
    text = (
        f"Client error '401' for url 'https://api.x.test/odds?apiKey=k123&r=eu' "
        f"token {token} raw {secret} encoded {encoded}"
    )
    clean = redact_text(text)
    _assert_absent("k123", clean)
    _assert_absent(token, clean)
    _assert_absent(secret, clean)
    _assert_absent("s3cr3t%2Fvalue%2Bwith%3Dchars", clean)
    assert f"apiKey={REDACTED}&r=eu" in clean


def test_short_values_are_not_registered() -> None:
    register_secret("abc", "", None)
    assert redact_text("abc is fine") == "abc is fine"


def test_redact_headers() -> None:
    headers = {
        "Authorization": "Bearer sk-live-123456789",
        "x-apisports-key": "k1",
        "X-RapidAPI-Key": "k2",
        "x-api-key": "k3",
        "x-auth-token": "k4",
        "Cookie": "bp_refresh=abc",
        "Accept": "application/json",
    }
    clean = redact_headers(headers)
    assert clean["Accept"] == "application/json"
    assert {v for k, v in clean.items() if k != "Accept"} == {REDACTED}


# --- clients ------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_telegram_errors_never_carry_the_bot_token() -> None:
    token = _telegram_token()
    settings = get_settings().model_copy(
        update={"telegram_bot_token": token, "telegram_api_base_url": "https://api.telegram.org"}
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    respx.post(url).mock(side_effect=connect_error)
    with pytest.raises(PushError) as excinfo:
        await send_telegram(settings, "42", "hi")
    _assert_absent(token, _rendered(excinfo.value))

    respx.post(url).mock(return_value=httpx.Response(500, text=f"echo {token}"))
    with pytest.raises(PushError) as excinfo:
        await send_telegram(settings, "42", "hi")
    _assert_absent(token, _rendered(excinfo.value))


@pytest.mark.asyncio
@respx.mock
async def test_odds_api_style_query_key_is_scrubbed() -> None:
    key = secrets.token_hex(16)
    respx.get("https://api.the-odds-api.com/v4/sports").mock(
        return_value=httpx.Response(401, json={"message": f"bad key {key}"})
    )
    async with outbound_client(secrets=[key], timeout=5) as client:
        resp = await client.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": key})
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            check_status(resp)
    exc = excinfo.value
    _assert_absent(key, _rendered(exc), str(exc.response.request.url), exc.response.text)
    assert exc.response.status_code == 401
    assert f"apiKey={REDACTED}" in str(exc)


@pytest.mark.asyncio
@respx.mock
async def test_transport_errors_keep_their_class_but_lose_the_key() -> None:
    key = secrets.token_hex(16)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(f"timed out: {request.url}", request=request)

    respx.get(host="api.the-odds-api.com").mock(side_effect=timeout)
    async with outbound_client(timeout=5) as client:
        with pytest.raises(httpx.ConnectTimeout) as excinfo:
            await client.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": key})
    exc = excinfo.value
    assert isinstance(exc, httpx.TimeoutException)
    assert exc.__context__ is None and exc.__cause__ is None
    _assert_absent(key, _rendered(exc))


@pytest.mark.asyncio
@respx.mock
async def test_sportmonks_style_token_and_header_are_scrubbed() -> None:
    key = secrets.token_hex(20)
    respx.get(host="api.sportmonks.com").mock(return_value=httpx.Response(500))
    async with outbound_client(secrets=[key], headers={"Authorization": key}, timeout=5) as client:
        resp = await client.get(
            "https://api.sportmonks.com/v3/football/fixtures", params={"api_token": key}
        )
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            check_status(resp)
    exc = excinfo.value
    _assert_absent(key, _rendered(exc), str(exc.request.headers))


@pytest.mark.asyncio
@respx.mock
async def test_api_football_key_never_reaches_the_error() -> None:
    key = secrets.token_hex(16)
    respx.get("https://v3.football.api-sports.io/status").mock(
        return_value=httpx.Response(403, json={"errors": {"token": f"invalid {key}"}})
    )
    provider = ApiFootballProvider(api_key=key)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await provider._get("/status")
    _assert_absent(key, _rendered(excinfo.value), str(excinfo.value.request.headers))


@pytest.mark.asyncio
@respx.mock
async def test_openai_sdk_error_does_not_carry_the_key(log_output: io.StringIO) -> None:
    """The SDK is the one documented client outside outbound_client."""
    key = "sk-" + secrets.token_urlsafe(32)
    register_secret(key)
    respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Incorrect API key"}})
    )
    client = openai.AsyncOpenAI(api_key=key, base_url="https://llm.test/v1", max_retries=0)
    with pytest.raises(openai.AuthenticationError) as excinfo:
        await client.chat.completions.create(
            model="m", messages=[{"role": "user", "content": "hi"}]
        )
    _assert_absent(key, _rendered(excinfo.value))
    logging.getLogger("app.llm").exception("llm failed", exc_info=excinfo.value)
    _assert_absent(key, log_output.getvalue())


# --- log safety net -------------------------------------------------------------


def test_raw_httpx_error_logged_outside_the_wrapper_is_scrubbed(log_output: io.StringIO) -> None:
    key = secrets.token_hex(16)
    token = _telegram_token()
    request = httpx.Request("GET", f"https://api.the-odds-api.com/v4/odds?apiKey={key}")
    raw = httpx.HTTPStatusError(
        f"Client error '401 Unauthorized' for url '{request.url}'",
        request=request,
        response=httpx.Response(401, request=request),
    )
    # ARQ's failure-line format; a neutral logger name, since other tests
    # reconfigure the real "arq" loggers.
    logger = logging.getLogger("tests.arq_like")
    try:
        raise raw
    except httpx.HTTPStatusError as exc:
        # ARQ's own failure line: message args plus the traceback.
        logger.exception("%6.2fs ! %s failed, %s: %s", 0.5, "job:poll", type(exc).__name__, exc)
    logging.getLogger("app").warning("sending to https://api.telegram.org/bot%s/x", token)
    logging.getLogger("app").error("db at %s", "postgresql+asyncpg://football:pw12345678@pg/f")

    output = log_output.getvalue()
    _assert_absent(key, output)
    _assert_absent(token, output)
    _assert_absent("pw12345678", output)
    assert "job:poll failed, HTTPStatusError" in output
    assert f"apiKey={REDACTED}" in output


def test_log_records_without_secrets_keep_their_arguments() -> None:
    """uvicorn's access formatter reads record.args as a tuple: untouched."""
    install_log_safety()
    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/health", "1.1", 200),
        None,
    )
    assert record.args == ("127.0.0.1:5000", "GET", "/health", "1.1", 200)


def test_httpx_stays_quiet_even_at_debug(log_output: io.StringIO) -> None:
    key = secrets.token_hex(16)
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        install_log_safety()
        for name in QUIET_LOGGERS:
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
            logging.getLogger(name).info(
                'HTTP Request: GET https://api.x.test/?apiKey=%s "HTTP/1.1 200 OK"', key
            )
            logging.getLogger(name).debug("Request options: %s", {"api_key": key})
        logging.getLogger("app.anything").debug("app debug still logs")
    finally:
        root.setLevel(previous)
    output = log_output.getvalue()
    assert "HTTP Request" not in output
    assert "Request options" not in output
    assert "app debug still logs" in output


def test_secret_in_the_template_itself_is_scrubbed(log_output: io.StringIO) -> None:
    key = secrets.token_hex(16)
    logging.getLogger("app").warning(
        "GET https://api.the-odds-api.com/v4/odds?apiKey=" + key + " -> %d", 401
    )
    output = log_output.getvalue()
    _assert_absent(key, output)
    assert f"apiKey={REDACTED} -> 401" in output


def test_secret_in_a_tuple_argument_keeps_the_tuple(log_output: io.StringIO) -> None:
    key = secrets.token_hex(16)
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/cb?token={key}", "1.1", 200),
        None,
    )
    assert isinstance(record.args, tuple) and len(record.args) == 5
    _assert_absent(key, record.getMessage())


def test_a_malformed_record_never_breaks_the_factory() -> None:
    install_log_safety()
    record = logging.getLogger("app").makeRecord(
        "app", logging.WARNING, __file__, 1, "two placeholders %s %s", ("only-one",), None
    )
    assert record.args == ("only-one",)  # left for logging's own error path
