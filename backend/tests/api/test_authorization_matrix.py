"""Who may call which route: one policy table, checked three ways.

D — route guard. ``ROUTE_POLICY`` has exactly one row per (method, path) the
app serves; a new route fails until it is classified here, and a removed one
fails until its row goes. Each row must agree with the route's real dependency
tree (``require_admin``, ``get_current_user``, the tier gates, CSRF), every
``/admin/*`` route must be admin, and public routes stay a short, commented list.

B — authorization matrix. Every route is called over HTTP by a set of
identities, and the answer must match its policy: 401 for anonymous, garbage,
expired and inactive callers on anything that needs a user; 403 for the wrong
role or tier; and "allowed" (anything but 401/403 — random ids and empty bodies
give 404/422) for the right caller. Plus BOLA checks for the user-owned
resources (strategies, push subscriptions, follows).
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import pytest_asyncio
from app.core.arq import get_arq_pool
from app.core.config import get_settings
from app.core.security import ACCESS_TOKEN_TYPE, create_access_token
from app.main import app, create_app
from app.models.backtester import Strategy
from app.models.fixture import Fixture, FixtureStatus
from app.models.live import PushChannel, PushFollow, PushSubscription
from app.models.reference import League, Team
from app.models.user import User, UserRole, UserTier
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class Policy(enum.StrEnum):
    PUBLIC = "public"  # nobody needs to sign in
    PUBLIC_TIER = "public_tier"  # public; the tier (guest by IP or user) shapes the answer
    USER = "user"  # any signed-in, active user
    PUSH_TIER = "push_tier"  # signed in, tier may receive pushes (pro/expert)
    STREAM_TIER = "stream_tier"  # signed in, tier has live_recompute (pro/expert)
    CSRF = "csrf"  # refresh-cookie routes: double-submit CSRF token
    ADMIN = "admin"  # require_admin: admin role, password changed, 2FA on


A, U, PT, ST, C = Policy.ADMIN, Policy.USER, Policy.PUSH_TIER, Policy.STREAM_TIER, Policy.CSRF

# Public routes, each with the reason it needs no sign-in. Keep this list short:
# adding a row here is a security decision, not a convenience.
PUBLIC_ROUTES: dict[tuple[str, str], tuple[Policy, str]] = {
    ("GET", "/health"): (Policy.PUBLIC, "liveness probe"),
    ("GET", "/health/ready"): (Policy.PUBLIC, "readiness probe (deploy scripts, BFF /api/ready)"),
    ("GET", "/performance"): (Policy.PUBLIC, "public model-quality page"),
    ("GET", "/push/vapid-public-key"): (Policy.PUBLIC, "VAPID public key, public by design"),
    ("POST", "/auth/login"): (Policy.PUBLIC, "sign-in itself"),
    ("POST", "/auth/register"): (Policy.PUBLIC, "sign-up itself (rate limit: backlog O2)"),
    ("POST", "/auth/verify-email"): (Policy.PUBLIC, "the emailed token is the credential"),
    ("POST", "/push/telegram/webhook"): (
        Policy.PUBLIC,
        "Telegram calls it; authenticated by the webhook secret header",
    ),
    ("GET", "/matches"): (Policy.PUBLIC_TIER, "match list; guests limited by tier"),
    ("GET", "/matches/{fixture_id}"): (Policy.PUBLIC_TIER, "match card; guest quota by IP"),
    ("GET", "/matches/{fixture_id}/analysis"): (
        Policy.PUBLIC_TIER,
        "LLM analysis; access by tier and the match's rank",
    ),
}
MAX_PUBLIC_ROUTES = 11

ROUTE_POLICY: dict[tuple[str, str], Policy] = {
    **{key: policy for key, (policy, _) in PUBLIC_ROUTES.items()},
    # auth
    ("GET", "/auth/me"): U,
    ("POST", "/auth/2fa/setup"): U,
    ("POST", "/auth/2fa/enable"): U,
    ("POST", "/auth/2fa/disable"): U,
    ("POST", "/auth/change-password"): U,
    ("POST", "/auth/refresh"): C,
    ("POST", "/auth/logout"): C,
    # user features
    ("POST", "/backtester/run"): U,
    ("GET", "/backtester/strategies"): U,
    ("POST", "/backtester/strategies"): U,
    ("DELETE", "/backtester/strategies/{strategy_id}"): U,
    ("GET", "/backtester/strategies/{strategy_id}/export.csv"): U,
    ("POST", "/promo/redeem"): U,
    ("GET", "/push/subscriptions"): U,
    ("DELETE", "/push/subscriptions/{subscription_id}"): U,
    ("DELETE", "/push/telegram"): U,
    ("POST", "/push/telegram/link"): PT,
    ("GET", "/live/push/follows"): U,
    ("PUT", "/live/push/follow/{fixture_id}"): PT,
    ("DELETE", "/live/push/follow/{fixture_id}"): U,  # a downgraded user may still clean up
    ("POST", "/live/push/subscribe"): PT,
    ("GET", "/live/stream"): ST,
    # admin
    ("GET", "/admin/ping"): A,
    ("GET", "/admin/audit"): A,
    ("GET", "/admin/system/health"): A,
    ("POST", "/admin/system/alerts/test"): A,
    ("GET", "/admin/providers"): A,
    ("POST", "/admin/providers"): A,
    ("PATCH", "/admin/providers/{provider_id}"): A,
    ("DELETE", "/admin/providers/{provider_id}"): A,
    ("POST", "/admin/providers/{provider_id}/enable"): A,
    ("POST", "/admin/providers/{provider_id}/disable"): A,
    ("GET", "/admin/ingestion/runs"): A,
    ("POST", "/admin/ingestion/rescan"): A,
    ("GET", "/admin/models"): A,
    ("PATCH", "/admin/models/{model_id}"): A,
    ("POST", "/admin/models/{model_id}/promote"): A,
    ("POST", "/admin/models/{model_id}/demote"): A,
    ("PUT", "/admin/models/weighting"): A,
    ("PUT", "/admin/models/weights"): A,
    ("POST", "/admin/models/retrain"): A,
    ("GET", "/admin/models/snapshots"): A,
    ("GET", "/admin/models/snapshots/{snapshot_id}/diff"): A,
    ("POST", "/admin/models/rollback/{snapshot_id}"): A,
    ("GET", "/admin/llm-config"): A,
    ("PATCH", "/admin/llm-config"): A,
    ("GET", "/admin/llm/spend"): A,
    ("GET", "/admin/users"): A,
    ("POST", "/admin/users/{user_id}/tier"): A,
    ("GET", "/admin/users/{user_id}/redemptions"): A,
    ("POST", "/admin/users/{user_id}/disable"): A,
    ("POST", "/admin/users/{user_id}/enable"): A,
    ("GET", "/admin/tiers"): A,
    ("PATCH", "/admin/tiers/{tier_id}"): A,
    ("GET", "/admin/promo/batches"): A,
    ("POST", "/admin/promo/batches"): A,
    ("GET", "/admin/promo/batches/{batch_id}/export.csv"): A,
    ("POST", "/admin/promo/batches/{batch_id}/kill"): A,
}

# USER routes whose handler adds its own tier gate (after auth, so not visible in
# the dependency tree): a signed-in user below that tier gets 403. The gate
# itself is tested with the feature (tests/api/test_backtester.py). Routes whose
# body is validated before the handler runs (e.g. POST /backtester/strategies,
# expert-only too) answer 422 to an empty body and need no entry.
HANDLER_TIER_GATES: dict[tuple[str, str], str] = {
    ("GET", "/backtester/strategies/{strategy_id}/export.csv"): "CSV export is expert-only",
}

# Routes whose "allowed" call is not made, each with the reason. Their denials
# are still checked.
ALLOWED_CALL_SKIPPED: dict[tuple[str, str], str] = {
    ("GET", "/live/stream"): "an allowed call opens an SSE stream that never ends",
}


# --- D: route guard -------------------------------------------------------------


def _dependency_names(dependant: Dependant) -> set[str]:
    names: set[str] = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            names.add(getattr(sub.call, "__name__", repr(sub.call)))
        names |= _dependency_names(sub)
    return names


def _app_routes() -> dict[tuple[str, str], set[str]]:
    routes: dict[tuple[str, str], set[str]] = {}
    for context in iter_route_contexts(create_app().routes):
        route = context.route
        if isinstance(route, APIRoute):
            names = _dependency_names(route.dependant)
            for method in route.methods or ():
                routes[(method, route.path)] = names
    return routes


def _policy_from_dependencies(names: set[str]) -> Policy:
    if "require_admin" in names:
        return Policy.ADMIN
    if "require_streaming_tier" in names:
        return Policy.STREAM_TIER
    if "require_push_tier" in names:
        return Policy.PUSH_TIER
    if "get_current_user" in names:
        return Policy.USER
    if "verify_csrf" in names:
        return Policy.CSRF
    if "get_tier_context" in names or "get_optional_user" in names:
        return Policy.PUBLIC_TIER
    return Policy.PUBLIC


def test_policy_table_has_one_row_per_route() -> None:
    routes = _app_routes()
    assert len(routes) >= 69, "the route walk found too few routes"
    missing = sorted(set(routes) - set(ROUTE_POLICY))
    stale = sorted(set(ROUTE_POLICY) - set(routes))
    assert not missing, f"classify these new routes in ROUTE_POLICY: {missing}"
    assert not stale, f"remove these rows, the routes are gone: {stale}"


def test_policy_table_matches_each_routes_dependencies() -> None:
    wrong = {
        key: (ROUTE_POLICY[key].value, _policy_from_dependencies(names).value)
        for key, names in _app_routes().items()
        if key in ROUTE_POLICY and ROUTE_POLICY[key] != _policy_from_dependencies(names)
    }
    assert not wrong, f"(table, dependencies) disagree: {wrong}"


def test_every_admin_path_requires_admin() -> None:
    not_admin = [k for k, p in ROUTE_POLICY.items() if k[1].startswith("/admin") and p != A]
    assert not not_admin


def test_public_routes_are_few_and_each_has_a_reason() -> None:
    public = {k for k, p in ROUTE_POLICY.items() if p in (Policy.PUBLIC, Policy.PUBLIC_TIER)}
    assert public == set(PUBLIC_ROUTES)
    assert len(public) <= MAX_PUBLIC_ROUTES
    assert all(reason.strip() for _, reason in PUBLIC_ROUTES.values())


# --- B: authorization matrix -------------------------------------------------------


class _FakeArq:
    async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest_asyncio.fixture
async def no_arq() -> AsyncIterator[None]:
    """Admin actions that enqueue a job (retrain, rescan) talk to a fake pool."""
    app.dependency_overrides[get_arq_pool] = lambda: _FakeArq()
    yield
    app.dependency_overrides.pop(get_arq_pool, None)


async def _user(session: AsyncSession, **fields: Any) -> User:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", **fields)
    session.add(user)
    await session.commit()
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}


def _expired_bearer(user: User) -> dict[str, str]:
    settings = get_settings()
    past = datetime.now(UTC) - timedelta(hours=2)
    claims = {
        "sub": str(user.id),
        "role": user.role.value,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(past.timestamp()),
        "exp": int((past + timedelta(minutes=15)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {token}"}


async def _identities(session: AsyncSession) -> dict[str, dict[str, str]]:
    admin_fields: dict[str, Any] = {
        "role": UserRole.admin,
        "must_change_password": False,
        "totp_enabled": True,
    }
    free = await _user(session, tier=UserTier.free)
    return {
        "anonymous": {},
        "garbage token": {"Authorization": "Bearer garbage"},
        "expired token": _expired_bearer(free),
        "inactive user": _bearer(await _user(session, is_active=False)),
        "inactive admin": _bearer(await _user(session, **{**admin_fields, "is_active": False})),
        "free user": _bearer(free),
        "expert user": _bearer(await _user(session, tier=UserTier.expert)),
        "admin without 2FA": _bearer(
            await _user(session, **{**admin_fields, "totp_enabled": False})
        ),
        "admin must change password": _bearer(
            await _user(session, **{**admin_fields, "must_change_password": True})
        ),
        "admin": _bearer(await _user(session, **admin_fields)),
    }


UNAUTHENTICATED = ("anonymous", "garbage token", "expired token", "inactive user", "inactive admin")
NOT_ADMIN = ("free user", "expert user", "admin without 2FA", "admin must change password")
ALLOWED, DENIED_401, DENIED_403 = "allowed", 401, 403


def _expectations(key: tuple[str, str]) -> dict[str, object]:
    """identity -> 401, 403 or ALLOWED (anything but 401/403)."""
    policy = ROUTE_POLICY[key]
    if key in HANDLER_TIER_GATES:  # USER route, expert-only in the handler
        return dict.fromkeys(UNAUTHENTICATED, DENIED_401) | {
            "free user": DENIED_403,
            "admin": DENIED_403,  # admins are on the free tier unless granted one
            "expert user": ALLOWED,
        }
    if policy in (Policy.PUBLIC, Policy.PUBLIC_TIER):
        return {"anonymous": ALLOWED, "free user": ALLOWED, "admin": ALLOWED}
    if policy == Policy.CSRF:
        # No CSRF token: refused whoever calls (the cookie path is tested below).
        return {"anonymous": DENIED_403, "free user": DENIED_403, "admin": DENIED_403}
    expected: dict[str, object] = dict.fromkeys(UNAUTHENTICATED, DENIED_401)
    if policy == Policy.ADMIN:
        expected |= dict.fromkeys(NOT_ADMIN, DENIED_403)
        expected["admin"] = ALLOWED
    elif policy == Policy.USER:
        expected |= {"free user": ALLOWED, "expert user": ALLOWED, "admin": ALLOWED}
    else:  # PUSH_TIER / STREAM_TIER: free is refused, pro/expert pass
        expected |= {"free user": DENIED_403, "expert user": ALLOWED}
    return expected


def _concrete(path: str) -> str:
    """A random UUID for every path parameter."""
    parts = [str(uuid.uuid4()) if p.startswith("{") else p for p in path.split("/")]
    return "/".join(parts)


@pytest.mark.parametrize(
    ("method", "path"), sorted(ROUTE_POLICY), ids=[f"{m} {p}" for m, p in sorted(ROUTE_POLICY)]
)
@pytest.mark.usefixtures("no_arq")
async def test_authorization_matrix(
    client: AsyncClient, session: AsyncSession, method: str, path: str
) -> None:
    identities = await _identities(session)
    url = _concrete(path)
    failures = []
    for identity, expected in _expectations((method, path)).items():
        if expected == ALLOWED and (method, path) in ALLOWED_CALL_SKIPPED:
            continue
        status = (await client.request(method, url, headers=identities[identity])).status_code
        ok = status not in (401, 403) if expected == ALLOWED else status == expected
        if not ok:
            failures.append(f"{identity}: got {status}, expected {expected}")
    assert not failures, f"{method} {path}: " + "; ".join(failures)


def test_exceptions_are_listed_with_a_reason() -> None:
    for table in (ALLOWED_CALL_SKIPPED, HANDLER_TIER_GATES):
        assert set(table) <= set(ROUTE_POLICY)
        assert all(reason.strip() for reason in table.values())
    assert all(ROUTE_POLICY[k] == U for k in HANDLER_TIER_GATES)


async def test_csrf_routes_pass_the_check_with_a_matching_token(client: AsyncClient) -> None:
    settings = get_settings()
    token = "csrf-token-for-the-matrix"  # noqa: S105
    client.cookies.set(settings.csrf_cookie_name, token)
    for path in ("/auth/refresh", "/auth/logout"):
        resp = await client.post(path, headers={"X-CSRF-Token": token})
        assert resp.status_code != 403, path  # past CSRF: no refresh cookie -> 401 / 204
        mismatch = await client.post(path, headers={"X-CSRF-Token": "other"})
        assert mismatch.status_code == 403, path


async def test_auth_is_checked_before_the_body(client: AsyncClient, session: AsyncSession) -> None:
    """An admin route with an invalid body answers 403 to a user, not 422."""
    headers = _bearer(await _user(session))
    resp = await client.post("/admin/promo/batches", headers=headers, json={"size": "x"})
    assert resp.status_code == 403


# --- BOLA: user A against user B's resources -------------------------------------------


async def _fixture(session: AsyncSession) -> uuid.UUID:
    league = League(code=f"L{uuid.uuid4().hex[:4]}", name="League")
    home = Team(name="Home", normalized_name=f"h-{uuid.uuid4().hex[:6]}")
    away = Team(name="Away", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    session.add_all([league, home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(UTC) + timedelta(hours=2),
        status=FixtureStatus.scheduled,
    )
    session.add(fixture)
    await session.commit()
    return fixture.id


async def test_bola_strategies(client: AsyncClient, session: AsyncSession) -> None:
    a = await _user(session, tier=UserTier.expert)
    b = await _user(session, tier=UserTier.expert)
    strategy = Strategy(user_id=b.id, name="b's", filters={}, bet_type="1x2", pick="home")
    session.add(strategy)
    await session.commit()
    sid = strategy.id

    listed = await client.get("/backtester/strategies", headers=_bearer(a))
    assert listed.status_code == 200 and str(sid) not in listed.text
    export = await client.get(f"/backtester/strategies/{sid}/export.csv", headers=_bearer(a))
    assert export.status_code == 404
    delete = await client.delete(f"/backtester/strategies/{sid}", headers=_bearer(a))
    assert delete.status_code == 404
    session.expire_all()
    assert await session.get(Strategy, sid) is not None


async def test_bola_push_subscriptions(client: AsyncClient, session: AsyncSession) -> None:
    a = await _user(session, tier=UserTier.expert)
    b = await _user(session, tier=UserTier.expert)
    sub = PushSubscription(
        user_id=b.id, channel=PushChannel.webpush, endpoint="https://fcm.googleapis.com/x"
    )
    session.add(sub)
    await session.commit()
    sub_id = sub.id

    listed = await client.get("/push/subscriptions", headers=_bearer(a))
    assert listed.status_code == 200 and str(sub_id) not in listed.text
    # The delete is scoped to the caller (WHERE user_id = caller) and idempotent:
    # it answers 204 without touching b's row.
    delete = await client.delete(f"/push/subscriptions/{sub_id}", headers=_bearer(a))
    assert delete.status_code == 204
    count = await session.scalar(
        select(func.count()).select_from(PushSubscription).where(PushSubscription.id == sub_id)
    )
    assert count == 1


async def test_bola_follows(client: AsyncClient, session: AsyncSession) -> None:
    a = await _user(session, tier=UserTier.expert)
    b = await _user(session, tier=UserTier.expert)
    fixture_id = await _fixture(session)
    session.add(PushFollow(user_id=b.id, fixture_id=fixture_id))
    await session.commit()

    listed = await client.get("/live/push/follows", headers=_bearer(a))
    assert listed.status_code == 200 and str(fixture_id) not in listed.text
    # Unfollow is keyed by (caller, fixture): a's call removes only a's own
    # follow, so b keeps following.
    unfollow = await client.delete(f"/live/push/follow/{fixture_id}", headers=_bearer(a))
    assert unfollow.status_code == 200
    count = await session.scalar(
        select(func.count())
        .select_from(PushFollow)
        .where(PushFollow.user_id == b.id, PushFollow.fixture_id == fixture_id)
    )
    assert count == 1
