"""Base class for request bodies.

Every model FastAPI parses as a request body, and every model nested in one,
derives from :class:`RequestModel`: an unknown field is a 422, never silently
dropped (a misspelt ``api_key`` must not create a provider without its key).
``tests/api/test_request_validation.py`` walks the app's routes and fails on any
body model that does not forbid extra fields. Response models and provider DTOs
stay lenient on purpose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
