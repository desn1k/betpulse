"""The only door for outbound HTTP, and the scrubber that keeps keys out of logs.

Provider keys travel in URLs (The Odds API ``?apiKey=``, Sportmonks
``?api_token=``, the Telegram bot token in the path) and in headers. httpx puts
the full URL into exception text (``... for url 'https://...?apiKey=...'``) and
into its INFO log line, and ARQ logs a failed job's exception text and
traceback. So:

1. **Clients.** Every outbound call goes through :func:`outbound_client` (a
   ``httpx.AsyncClient`` whose ``send`` re-raises any ``httpx.HTTPError`` as a
   scrubbed copy of the same class, with no exception chain) and
   :func:`check_status` instead of ``raise_for_status``. The copies keep their
   httpx class, so ``except httpx.HTTPError`` / ``httpx.TimeoutException`` keep
   working, but their message, ``request`` and ``response`` are rebuilt from the
   redacted URL: the original request (and its headers) is not reachable.
   ``tests/test_outbound_guard.py`` fails on any other HTTP client in ``app/``.
   The one documented exception is the OpenAI SDK (LLM analysis): its key is a
   header, and it is registered as a secret for the log safety net.
2. **Log safety net.** :func:`install_log_safety` (called by every process
   entry point) wraps the ``LogRecord`` factory, so every record of every logger
   (ours, ARQ, uvicorn, libraries) has its message, arguments, exception text and
   stack redacted before any handler sees it. It also pins the ``httpx``,
   ``httpcore`` and ``openai`` loggers to WARNING: their INFO/DEBUG lines carry
   full URLs or request options, whatever level the application logs at.

Redaction: URL userinfo, sensitive query parameters (:data:`SENSITIVE_QUERY_PARAMS`),
Telegram bot tokens (in a ``/bot<token>/`` path or anywhere in text), sensitive
headers (:data:`SENSITIVE_HEADERS`), and every registered secret value
(:func:`register_secret`; settings secrets at start-up, keys loaded from the
database by the client that uses them), raw and URL-encoded.
"""

from __future__ import annotations

import logging
import re
import threading
import traceback
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

REDACTED = "REDACTED"

# Query parameter names whose values are secrets (compared lower-case).
SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "apikey",  # The Odds API
        "api_key",
        "api_token",  # Sportmonks
        "key",
        "token",
        "access_token",
        "refresh_token",
        "auth",
        "secret",
        "client_secret",
        "password",
        "signature",
        "sig",
    }
)

# Header names whose values are secrets (compared lower-case).
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",  # Sportmonks, LLM (Bearer), Web Push (VAPID)
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-apisports-key",  # API-Football
        "x-rapidapi-key",  # API-Football via RapidAPI
        "x-api-key",
        "api-key",
        "x-auth-token",
    }
)

# Registered values shorter than this are ignored: redacting short strings
# would mangle ordinary text.
MIN_SECRET_LENGTH = 8

# (?<!\d), not \b: the token follows "bot" in a URL path with no word boundary.
_TELEGRAM_TOKEN = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")
_BOT_PATH = re.compile(r"/bot[^/\s?#'\"]+")
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>`]+")

# A sensitive query parameter outside a full URL (e.g. a bare path in an access log).
_QUERY_PARAM = re.compile(
    r"([?&](?:" + "|".join(sorted(SENSITIVE_QUERY_PARAMS, key=len, reverse=True)) + r")=)"
    r"([^&\s'\"<>#]+)",
    re.I,
)

_secrets: set[str] = set()
_secrets_lock = threading.Lock()


def register_secret(*values: str | None) -> None:
    """Redact these values wherever they appear (raw or URL-encoded)."""
    with _secrets_lock:
        for value in values:
            if value and len(value) >= MIN_SECRET_LENGTH:
                _secrets.add(value)
                encoded = quote(value, safe="")
                if encoded != value:
                    _secrets.add(encoded)


def registered_secrets() -> frozenset[str]:
    with _secrets_lock:
        return frozenset(_secrets)


def _redact_query(query: str) -> str:
    parts = []
    for part in query.split("&"):
        name, sep, _ = part.partition("=")
        if sep and unquote(name).strip().lower() in SENSITIVE_QUERY_PARAMS:
            parts.append(f"{name}={REDACTED}")
        else:
            parts.append(part)
    return "&".join(parts)


def redact_url(url: str) -> str:
    """The URL with userinfo, sensitive query values and a bot token removed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text_basic(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rpartition("@")[2]
    path = _BOT_PATH.sub("/bot***", parts.path)
    query = _redact_query(parts.query) if parts.query else ""
    return urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


def redact_text_basic(text: str) -> str:
    """Patterns only: bare sensitive query parameters, Telegram tokens and
    registered secret values."""
    text = _QUERY_PARAM.sub(lambda m: m.group(1) + REDACTED, text)
    text = _TELEGRAM_TOKEN.sub("***", text)
    for secret in sorted(registered_secrets(), key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "***")
    return text


def redact_text(text: str) -> str:
    """Redact every URL in ``text``, then token patterns and registered secrets."""
    if not text:
        return text
    text = _URL.sub(lambda m: redact_url(m.group(0)), text)
    return redact_text_basic(text)


def redact_headers(headers: Mapping[str, str] | httpx.Headers) -> dict[str, str]:
    """A copy of ``headers`` safe to log."""
    return {
        name: (REDACTED if name.lower() in SENSITIVE_HEADERS else redact_text(value))
        for name, value in headers.items()
    }


# --- scrubbed exceptions ----------------------------------------------------


def _scrubbed_request(request: httpx.Request | None) -> httpx.Request:
    if request is None:
        return httpx.Request("GET", "https://redacted.invalid/")
    return httpx.Request(request.method, redact_url(str(request.url)))


def _request_of(exc: httpx.HTTPError) -> httpx.Request | None:
    try:
        return exc.request
    except RuntimeError:  # httpx raises when no request is attached
        return None


def scrub_exception(exc: httpx.HTTPError) -> httpx.HTTPError:
    """A copy of ``exc``, same class, with nothing secret in it."""
    request = _scrubbed_request(_request_of(exc))
    message = redact_text(str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        response = httpx.Response(exc.response.status_code, request=request)
        return httpx.HTTPStatusError(message, request=request, response=response)
    if isinstance(exc, httpx.RequestError):
        try:
            return type(exc)(message, request=request)
        except TypeError:
            return httpx.RequestError(message, request=request)
    return httpx.HTTPError(message)


class _ScrubbingAsyncClient(httpx.AsyncClient):
    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            return await super().send(request, **kwargs)
        except httpx.HTTPError as exc:
            scrubbed = scrub_exception(exc)
        # Raised outside the except block: no __context__ / __cause__ holding
        # the original exception, its URL and its request headers.
        raise scrubbed


def outbound_client(*, secrets: Iterable[str | None] = (), **kwargs: Any) -> httpx.AsyncClient:
    """The HTTP client for every outbound call. ``secrets``: values this client
    sends (keys, tokens) that must never be logged; ``kwargs`` go to
    ``httpx.AsyncClient``."""
    register_secret(*secrets)
    return _ScrubbingAsyncClient(**kwargs)


def check_status(response: httpx.Response) -> httpx.Response:
    """``raise_for_status`` with a scrubbed exception (no URL secrets, no body)."""
    if response.is_success:
        return response
    if 300 <= response.status_code < 400 and not response.is_error:
        kind = "Redirect response"
    elif response.status_code < 500:
        kind = "Client error"
    else:
        kind = "Server error"
    request = _scrubbed_request(response.request)
    message = f"{kind} '{response.status_code} {response.reason_phrase}' for url '{request.url}'"
    raise httpx.HTTPStatusError(
        message, request=request, response=httpx.Response(response.status_code, request=request)
    )


# --- log safety net ---------------------------------------------------------

# Libraries whose INFO/DEBUG lines carry full URLs or request options.
QUIET_LOGGERS = ("httpx", "httpcore", "openai")

_installed = False
_install_lock = threading.Lock()
_SCALARS = (int, float, bool, type(None))


def _redact_arg(value: Any) -> Any:
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, str):
        return redact_text(value)
    try:
        rendered = str(value)
    except Exception:
        return value
    clean = redact_text(rendered)
    # Keep the object (and its %r / %d formatting) unless it carries a secret.
    return value if clean == rendered else clean


def _scrub_message(record: logging.LogRecord) -> None:
    """Redact the rendered message. Records without a secret are left exactly as
    they are (uvicorn's access formatter reads ``record.args`` as a tuple)."""
    rendered = record.getMessage()
    clean = redact_text(rendered)
    if clean == rendered:
        return
    # Prefer redacting the arguments only, which keeps the record's structure;
    # the template itself is left alone so its %-placeholders stay intact.
    if record.args and redact_text_basic(str(record.msg)) == str(record.msg):
        if isinstance(record.args, Mapping):
            args: Any = {k: _redact_arg(v) for k, v in record.args.items()}
        else:
            args = tuple(_redact_arg(a) for a in record.args)
        try:
            candidate = str(record.msg) % args
        except (TypeError, ValueError, KeyError):
            candidate = None
        if candidate is not None and redact_text(candidate) == candidate:
            record.args = args
            return
    # The secret sits in the template or survives per-argument redaction:
    # keep the fully rendered, redacted text.
    record.msg, record.args = clean, None


def _scrub_record(record: logging.LogRecord) -> None:
    _scrub_message(record)
    if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
        rendered = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        record.exc_text = redact_text(rendered)
    if record.stack_info:
        record.stack_info = redact_text(record.stack_info)


def _settings_secrets() -> list[str]:
    from app.core.config import get_settings

    s = get_settings()
    values = [
        s.secret_key,
        s.data_encryption_key,
        s.api_football_key,
        s.telegram_bot_token,
        s.telegram_webhook_secret,
        s.webpush_vapid_private_key,
        s.redis_password,
    ]
    for url in (s.database_url, s.database_read_url, s.redis_url):
        password = urlsplit(url).password if url else None
        values.append(password or "")
    return values


def install_log_safety() -> None:
    """Install the redacting LogRecord factory and quiet URL-logging libraries.
    Idempotent; call it from every process entry point."""
    global _installed
    with _install_lock:
        for name in QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        try:
            register_secret(*_settings_secrets())
        except Exception:  # noqa: S110 - invalid settings fail elsewhere, loudly
            pass
        if _installed:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            try:
                _scrub_record(record)
            except Exception:  # noqa: S110 - logging must never fail on redaction
                pass
            return record

        logging.setLogRecordFactory(factory)
        _installed = True
