# Security

> **Status:** Phase 13 hardening complete. Response headers, sensitive rate
> limits, strict browser CSP, credentialed CORS restrictions, browser security
> tests, SQL-injection regressions, and reproducible staging DAST are in place.
> Production TLS/HSTS verification remains part of the Phase 14 release pass.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.
Email the maintainer at the address in the repository profile. Include steps to
reproduce, affected version/commit, and impact.

## Automated scanning (CI)

`security.yml` runs on every pull request and push to `main`:

| Tool | Scope |
|---|---|
| gitleaks | Secret scanning across history |
| bandit | Python SAST (`backend/app`) |
| semgrep | Multi-language SAST (`p/security-audit`, `p/secrets`) |
| pip-audit | Python dependency vulnerabilities |
| npm audit | JavaScript dependency vulnerabilities |
| trivy | Filesystem / dependency scan (HIGH, CRITICAL) |
| Playwright | Production browser boot, CSP nonces, headers, locale persistence |

Run the equivalent checks locally before opening a PR:

```bash
# Backend
cd backend && bandit -r app && pip-audit --skip-editable
# Frontend
cd frontend && npm audit --audit-level=high
npx playwright install --with-deps chromium
npm run test:e2e
```

## Security headers and browser CSP

Phase 13a adds a shared low-risk baseline to API and frontend responses:

| Header | Value |
|---|---|
| API `Content-Security-Policy` | `frame-ancestors 'none'` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |

Phase 13c replaces the frontend's static CSP with a fresh nonce on every
rendered request. Middleware forwards the nonce and full policy to Next.js and
returns the same policy to the browser. Production scripts require that nonce
and use `strict-dynamic`; objects and frames are disabled, `base-uri` and
`form-action` are restricted to same-origin, and service workers remain
available from `'self'`/`blob:`. Next.js development-only allowances are not
present in the production policy.

The root layout already reads request cookies, so rendered pages are dynamic;
this is required for per-request nonces and intentionally trades static page
caching for strict CSP.

## Sensitive rate limits

Every request limit is a Redis fixed window (`app/services/rate_limit.py`,
`app/services/limits.py`); every attempt counts, refused ones included, and an
excess answers 429 with `Retry-After`. Thresholds come from `RATE_LIMIT_*`
settings (`.env.example`).

| Surface | Identity | Threshold (default) | Position | Redis unavailable |
|---|---|---|---|---|
| `POST /auth/login` (password and TOTP step) | Client IP | 5 / 1 min | in the handler, before the user lookup and password hash | fails closed (500) |
| `POST /auth/login` | Account | exponential lockout after failures | user row (database, not Redis) | not affected |
| `POST /auth/refresh` (F7) | Client IP | 120 / 1 min | in the handler, before the rotation (a 429 rotates nothing, leaves the cookies) | **fails open**: no limit, a warning logged; the Redis call is bounded to 0.5 s |
| Refresh-token replay (F13 B) | Client IP | 10 successful replays / 1 min | inside the rotation, after the replay checks | fails open: no replay, the ordinary rotation rules apply |
| `GET /matches` list (F7) | Verified token subject, else guest IP | 120 / 60 s | route dependency, **before tier resolution** and any database read | fails closed (500) |
| `GET /matches/{id}` | Verified token subject, else guest IP | 120 / 60 s | route dependency, before tier resolution and the fixture lookup | fails closed (500) |
| `GET /matches/{id}/analysis` | Verified token subject, else guest IP | 20 / 1 min | route dependency, before tier resolution | fails closed (500) |
| `POST /promo/redeem` | User | 10 / clock hour | in the handler, before the code lookup | fails closed (500) |
| `POST /auth/change-password` | User | 5 / 15 min | before the current-password hash check | fails closed (500) |
| TOTP enable / disable (one bucket) | User | 5 / 15 min | before the code check | fails closed (500) |
| `POST /auth/2fa/setup` | User | 5 / 1 hour | before a new secret is generated | fails closed (500) |
| Admin unsafe mutations (`POST`, `PUT`, `PATCH`, `DELETE` under `/admin`) | Client IP | 60 / 1 min | middleware, before routing and authentication | fails closed (500) |

"Verified token subject" is the `sub` of an access token with a valid
signature, type and expiry, read without a database query
(`app.core.deps.rate_limit_identity`); a token that fails verification or has
no UUID subject counts as a guest. The key holds only the canonical UUID or the
guest's bucket, never token material. Why the refresh limit fails open: the
rotation itself needs only the database, so failing closed would stop every
signed-in user from renewing a session during a Redis outage; the routes that
fail closed need Redis for their own work anyway (quotas, lockout counters).

Daily product budgets (match detail views, backtester runs and delivered push
notifications) remain tier limits rather than abuse controls.

"Client IP" in these limits and in guest daily quotas is an IPv4 address or,
for IPv6, the client's /64 (a single subscriber usually controls a whole /64).
Audit log entries record the full address.

## Client IP and trusted proxies

Requests reach FastAPI through Caddy (only the Telegram webhook) or through the
Next.js BFF, so the TCP peer is one of our containers. `get_client_ip`
(`backend/app/core/client_ip.py`) is the single source of the client address:

- `X-Forwarded-For` is honoured only when the peer is inside
  `TRUSTED_PROXY_CIDRS`; from any other peer it is ignored and the peer
  address is used.
- Within a trusted chain the client is the right-most hop that is not itself a
  trusted proxy; left-hand, client-supplied entries are never used. Every hop
  must parse as an IP address, otherwise the walk stops at the last trusted
  hop. IPv4-mapped IPv6 is normalised to IPv4.
- uvicorn runs with `--no-proxy-headers` so the app always sees the raw peer.
- The Next.js BFF forwards exactly one hop: the right-most `X-Forwarded-For`
  entry it received (set by Caddy), and only if it is a valid IP. `X-Real-IP`
  is never read, because Caddy passes it through from the client. Every
  `app/api` route handler must call the backend through
  `frontend/lib/server/backendProxy.ts`; `routeHandlers.test.ts` fails the
  build otherwise. The `web` container must only be reachable through Caddy
  (production Compose publishes no `web` port).
- Docker Compose pins a dual-stack network (`BETPULSE_NETWORK_SUBNET`,
  `BETPULSE_NETWORK_SUBNET6`) and the `web` and `caddy` addresses in both
  families (`BETPULSE_WEB_IP(6)`, `BETPULSE_CADDY_IP(6)`) and trusts exactly
  those /32s and /128s. The Docker gateway and every other container are
  untrusted. The network must have IPv6: on an IPv4-only network Docker's
  userland proxy relays IPv6 connections to Caddy, which then sees every IPv6
  client as the gateway (F5). `scripts/check-compose-ports.sh` checks the
  network and the addresses; `scripts/edge-identity-smoke.sh` (CI) checks that
  IPv4 and IPv6 clients reach the upstream as themselves.
- `INTERNAL_NETWORK_CIDRS` names the network's subnets. A client identity inside
  them (the gateway, or an internal hop the API does not trust) is never a real
  client: each API process logs a warning on the first one, and the admin
  system health page shows them (`client_ip`, degraded for 24 h after the last
  one). Required in production; every trusted proxy must lie inside it.
- In production `TRUSTED_PROXY_CIDRS` must be set explicitly. Startup fails
  when it is missing, malformed, contains `0.0.0.0/0`/`::/0`, a public range, or
  a prefix broader than /16 (IPv6: /48). Outside production it defaults to
  loopback for local runs and tests.
- Caddy has no `trusted_proxies`, so it replaces any client-supplied
  `X-Forwarded-For` with the connecting address. `scripts/caddy-smoke.sh`
  asserts that a spoofed value never reaches an upstream.

**If a CDN, WAF or load balancer is ever placed in front of Caddy**, configure
Caddy's `trusted_proxies` with that provider's published ranges and re-review
the whole chain (Caddy, the BFF and `TRUSTED_PROXY_CIDRS`) before going live.
Otherwise every client appears as the CDN, and per-IP limits collapse into a
few shared buckets.

## Credentialed CORS

The API accepts credentialed browser requests only from the exact origins in
`CORS_ALLOWED_ORIGINS`. Methods and request headers use explicit allowlists;
production startup fails when the origin list contains `*`. Development keeps
the wildcard available for local tooling, but it is never a valid production
setting with credentials enabled.

## Security test gates

### Browser security on every pull request

`Browser security (Playwright)` builds and starts the production Next.js server
and runs Chromium against it. The check fails when:

- a rendered response has no CSP or reuses a nonce across requests;
- the production policy permits inline/evaluated scripts;
- the browser emits a CSP violation while booting the application;
- required response headers disappear or document CSP leaks onto `/api/*`;
- initial locale-cookie persistence stops working.

Failure traces, screenshots, videos, and the HTML report are retained as the
`playwright-security-report` workflow artifact for 14 days.

### Authorized staging DAST

`.github/workflows/dast.yml` is intentionally manual until Phase 14 provides a
stable staging deployment. It requires all of the following:

- an explicit HTTPS staging base URL;
- a same-host URL containing one safe query parameter for sqlmap;
- the parameter name;
- an explicit confirmation that the operator is authorized to scan the target.

The workflow rejects credentials in URLs, fragments, different sqlmap hosts,
and targets resolving to private, loopback, link-local, multicast, or reserved
addresses. Never point it at a third-party system or production without written
authorization and an agreed maintenance window.

Run it with GitHub CLI after replacing the example host:

```bash
gh workflow run dast.yml \
  -f target_url=https://staging.example.com \
  -f 'sqlmap_url=https://staging.example.com/api/matches?league=EPL' \
  -f sqlmap_parameter=league \
  -f confirm_authorized=true
```

The pinned scanner profiles are deliberately bounded:

| Scanner | Profile | Blocking result |
|---|---|---|
| OWASP ZAP 2.17.0 | Two-minute passive baseline | High risk with medium-or-higher confidence |
| Nuclei 3.8.0 | High/critical templates only | Any high or critical result |
| sqlmap 1.10.7 | One named parameter, level 1, risk 1, one thread | Confirmed injection point |

The workflow never requests database dumps, shells, destructive payloads, or
high-risk sqlmap tests. Raw logs plus ZAP HTML/Markdown/JSON, Nuclei JSONL, and
sqlmap output are retained in the `dast-reports` artifact for 30 days.

Expected clean output is a successful workflow with zero findings matching the
blocking rules. A scanner crash, missing report, unreachable URL, or failed
target validation also fails the workflow; it is not treated as a clean scan.

### False positives and exceptions

Do not silence a finding merely to make CI green. Reproduce it against the same
commit, preserve the report artifact, and record the scanner/rule ID, affected
URL, evidence, impact analysis, owner, and expiry in the PR discussion. A
suppression requires a focused configuration change reviewed like code; broad
scanner exclusions and permanent undocumented exceptions are not accepted.

## SQL-injection regression coverage

The matches, backtester, and audit filter tests submit SQL-shaped strings and
assert that they remain ordinary bound values. Typed numeric filters must return
validation errors; string filters must return controlled empty results without
authorization bypass, unrelated rows, data leakage, or server errors.

## Existing application controls

- Argon2id password hashing; JWT access + rotating refresh tokens; RBAC.
- Refresh rotation is atomic (per-family advisory lock + row lock); replay of a rotated token
  outside a 10 s duplicate window revokes the whole token family. Lockout counters, revocations
  and their audit rows are committed before the 401/429 is returned.
- Admin 2FA (TOTP) and full `audit_log`.
- Strict Pydantic input validation; parameterized queries only.
- HSTS at the production TLS edge (verified/finalized with Phase 14 release wiring).
- Redis rate limiting on auth and promo redemption; account lockout/backoff.
- Provider and LLM API keys encrypted at rest; never returned to the client.
- Secrets only from env/secret store; structured logging with secret redaction.
