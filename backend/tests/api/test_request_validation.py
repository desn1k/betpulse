"""Request bodies reject unknown fields, and a 422 never echoes what was sent.

Every model FastAPI parses as a request body (and every model nested in one)
must have ``extra="forbid"``: a typo'd or smuggled field is an error, not
silently dropped. The 422 body carries only ``type``, ``loc`` and ``msg`` per
error: pydantic's ``input`` (the submitted value: a password, an API key) and
``ctx`` never leave the server, and the log line written for a 422 names only
the error types and locations.
"""

from __future__ import annotations

import inspect
import logging
import pkgutil
import types
import uuid
from typing import Any, Union, get_args, get_origin

import app.schemas
import pytest
from app.core.security import create_access_token
from app.main import create_app
from app.models.reference import ProviderAccount
from app.models.user import User, UserRole, UserTier
from app.schemas.base import RequestModel
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Body models allowed to accept unknown fields. Keep it empty: a model that
# needs extra keys should declare them (or a typed mapping field) instead.
EXTRA_ALLOWED: frozenset[str] = frozenset()

# A value no 422 body or log line may contain.
MARKER = "sk-live-MARKER-7f3a9c"

_REQUEST_SUFFIXES = ("Request", "In", "Create", "Update", "Assign")


def _models_in(annotation: Any) -> set[type[BaseModel]]:
    """Every pydantic model reachable from a type annotation, recursively."""
    found: set[type[BaseModel]] = set()
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        found.add(annotation)
        for field in annotation.model_fields.values():
            found |= _models_in(field.annotation)
        return found
    if get_origin(annotation) in (Union, types.UnionType) or get_args(annotation):
        for arg in get_args(annotation):
            found |= _models_in(arg)
    return found


def _body_models() -> dict[str, set[str]]:
    """Model name → the routes that take it (directly or nested) as a body."""
    out: dict[str, set[str]] = {}
    # FastAPI keeps included routers nested; iter_route_contexts flattens them.
    for context in iter_route_contexts(create_app().routes):
        route = context.route
        if not isinstance(route, APIRoute) or route.body_field is None:
            continue
        for model in _models_in(route.body_field.field_info.annotation):
            out.setdefault(f"{model.__module__}.{model.__qualname__}", set()).add(
                f"{sorted(route.methods or ())} {route.path}"
            )
    return out


def _model_by_name(name: str) -> type[BaseModel]:
    module_name, _, qualname = name.rpartition(".")
    module = __import__(module_name, fromlist=[qualname])
    model: type[BaseModel] = getattr(module, qualname)
    return model


def test_every_request_body_model_forbids_extra_fields() -> None:
    bodies = _body_models()
    # The walk really found the request models (21 today, StrategyFilter nested).
    assert len(bodies) >= 21, sorted(bodies)
    offenders = {
        name: sorted(routes)
        for name, routes in bodies.items()
        if name not in EXTRA_ALLOWED and _model_by_name(name).model_config.get("extra") != "forbid"
    }
    assert not offenders, f"request body models without extra='forbid': {offenders}"


def test_extra_allowlist_stays_empty() -> None:
    assert EXTRA_ALLOWED == frozenset()


def test_request_named_schemas_inherit_request_model() -> None:
    """Catches a new request schema before a route uses it."""
    offenders = []
    for info in pkgutil.iter_modules(app.schemas.__path__):
        module = __import__(f"app.schemas.{info.name}", fromlist=["_"])
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj.__module__ == module.__name__
                and name.endswith(_REQUEST_SUFFIXES)
                and not issubclass(obj, RequestModel)
            ):
                offenders.append(f"{module.__name__}.{name}")
    assert not offenders, f"request schemas not based on RequestModel: {offenders}"


def _assert_no_echo(body: dict[str, Any], *values: str) -> None:
    text = str(body)
    for value in values:
        assert value not in text
    for error in body["detail"]:
        assert set(error) == {"type", "loc", "msg"}, error


async def _headers(session: AsyncSession, role: UserRole = UserRole.user) -> dict[str, str]:
    user = User(
        email=f"{uuid.uuid4()}@x.com",
        password_hash="x",
        role=role,
        tier=UserTier.expert,
        must_change_password=False,
        totp_enabled=role == UserRole.admin,
    )
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role=role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_register_rejects_an_extra_field_without_echoing_it(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/register",
        json={"email": f"{uuid.uuid4()}@x.com", "password": "long-enough-pass-1", "role": MARKER},
    )
    assert resp.status_code == 422
    body = resp.json()
    _assert_no_echo(body, MARKER)
    assert body["detail"] == [
        {
            "type": "extra_forbidden",
            "loc": ["body", "role"],
            "msg": "Extra inputs are not permitted",
        }
    ]


@pytest.mark.asyncio
async def test_login_rejects_an_extra_field(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login", json={"email": "a@x.com", "password": "whatever", "remember": MARKER}
    )
    assert resp.status_code == 422
    _assert_no_echo(resp.json(), MARKER)


@pytest.mark.asyncio
async def test_a_short_password_is_not_echoed(client: AsyncClient) -> None:
    password = "Pw-7f3a9c!"  # 10 chars, below the 12-char minimum
    resp = await client.post(
        "/auth/register", json={"email": f"{uuid.uuid4()}@x.com", "password": password}
    )
    assert resp.status_code == 422
    body = resp.json()
    _assert_no_echo(body, password)
    assert body["detail"][0]["type"] == "string_too_short"
    assert body["detail"][0]["loc"] == ["body", "password"]


@pytest.mark.asyncio
async def test_a_rejected_provider_key_is_not_echoed(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _headers(session, UserRole.admin)
    too_long_key = MARKER * 20  # over the 256-char limit
    resp = await client.post(
        "/admin/providers", json={"name": "odds", "api_key": too_long_key}, headers=headers
    )
    assert resp.status_code == 422
    _assert_no_echo(resp.json(), MARKER)

    # A misspelt key field is refused, not dropped (no provider without its key).
    resp = await client.post(
        "/admin/providers", json={"name": "odds", "apikey": MARKER}, headers=headers
    )
    assert resp.status_code == 422
    _assert_no_echo(resp.json(), MARKER)
    count = await session.scalar(select(func.count()).select_from(ProviderAccount))
    assert count == 0


@pytest.mark.asyncio
async def test_a_nested_extra_field_is_rejected(client: AsyncClient, session: AsyncSession) -> None:
    headers = await _headers(session)
    resp = await client.post(
        "/backtester/run",
        json={"bet_type": "1x2", "pick": "home", "filters": {"league": "EPL", "x": MARKER}},
        headers=headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    _assert_no_echo(body, MARKER)
    assert body["detail"][0]["loc"] == ["body", "filters", "x"]


@pytest.mark.asyncio
async def test_custom_value_error_messages_carry_no_submitted_values(
    client: AsyncClient, session: AsyncSession
) -> None:
    headers = await _headers(session)
    resp = await client.post(
        "/backtester/run", json={"bet_type": "1x2", "pick": MARKER}, headers=headers
    )
    assert resp.status_code == 422
    body = resp.json()
    _assert_no_echo(body, MARKER)
    assert body["detail"][0]["type"] == "value_error"
    assert body["detail"][0]["msg"].startswith("Value error, pick must be one of:")

    resp = await client.post(
        "/backtester/run",
        json={"bet_type": "1x2", "pick": "home", "filters": {"odds_min": 7.31, "odds_max": 1.29}},
        headers=headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    _assert_no_echo(body, "7.31", "1.29")
    assert body["detail"][0]["msg"] == (
        "Value error, odds_min must be less than or equal to odds_max"
    )


@pytest.mark.asyncio
async def test_query_validation_errors_are_not_echoed_either(client: AsyncClient) -> None:
    resp = await client.get("/matches", params={"limit": MARKER})
    assert resp.status_code == 422
    _assert_no_echo(resp.json(), MARKER)


@pytest.mark.asyncio
async def test_an_oversized_extra_key_name_is_truncated(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login", json={"email": "a@x.com", "password": "x", "k" * 500: 1}
    )
    assert resp.status_code == 422
    (error,) = resp.json()["detail"]
    assert error["loc"] == ["body", "k" * 64]


@pytest.mark.asyncio
async def test_the_422_log_line_names_locations_not_values(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    password = "Pw-7f3a9c!"
    with caplog.at_level(logging.INFO, logger="app.core.validation"):
        resp = await client.post(
            "/auth/register",
            json={"email": f"{uuid.uuid4()}@x.com", "password": password, "role": MARKER},
        )
    assert resp.status_code == 422
    records = [r for r in caplog.records if r.name == "app.core.validation"]
    assert len(records) == 1
    text = records[0].getMessage()
    assert "/auth/register" in text
    assert "string_too_short" in text and "extra_forbidden" in text
    assert password not in text and MARKER not in text
