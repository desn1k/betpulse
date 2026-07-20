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

Phase 13b adds or verifies Redis-backed limits on security-sensitive request
surfaces:

| Surface | Scope | Window | Default |
|---|---|---:|---:|
| Login attempts | Client IP | 1 minute | 5 |
| Promo redemption attempts | User | 1 hour | 10 |
| LLM analysis requests | User or guest IP | 1 minute | 20 |
| Admin unsafe mutations (`POST`, `PUT`, `PATCH`, `DELETE` under `/admin`) | Client IP | 1 minute | 60 |

Daily product budgets (match detail views, backtester runs and delivered push
notifications) remain tier limits rather than abuse controls.

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
- Admin 2FA (TOTP) and full `audit_log`.
- Strict Pydantic input validation; parameterized queries only.
- HSTS at the production TLS edge (verified/finalized with Phase 14 release wiring).
- Redis rate limiting on auth and promo redemption; account lockout/backoff.
- Provider and LLM API keys encrypted at rest; never returned to the client.
- Secrets only from env/secret store; structured logging with secret redaction.
