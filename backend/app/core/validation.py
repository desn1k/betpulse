"""The 422 response for request validation errors, without echoing the request.

FastAPI's default handler returns pydantic's error list as is, and each error
carries ``input`` (the submitted value: a too-short password, an over-long API
key, a smuggled extra field's value) and ``ctx``. This handler keeps only
``type``, ``loc`` and ``msg``, and caps each ``loc`` part, because the name of
an unknown field is chosen by the client too. ``msg`` is pydantic's text or the
message of one of our own validators, which never interpolates a submitted value
(``tests/api/test_request_validation.py``).

The log line names the method, path and the errors' ``type``/``loc`` only; it
goes through the log safety net (:func:`app.core.outbound.install_log_safety`)
like every other record.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Longest ``loc`` part returned or logged; longer client-chosen keys are cut.
MAX_LOC_PART = 64


def _loc_part(part: Any) -> str | int:
    if isinstance(part, int) and not isinstance(part, bool):
        return part
    return str(part)[:MAX_LOC_PART]


def sanitize_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Pydantic errors reduced to ``type``, ``loc`` and ``msg``."""
    return [
        {
            "type": str(error.get("type", "")),
            "loc": [_loc_part(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "")),
        }
        for error in errors
    ]


async def request_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # registered for this type only
        raise exc
    errors = sanitize_errors(exc.errors())
    logger.info(
        "request validation failed: %s %s %s",
        request.method,
        request.url.path,
        [(error["type"], error["loc"]) for error in errors],
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors}
    )
