# Security

> **Status:** Phase 13 hardening in progress. Response headers, sensitive
> rate limits, strict browser CSP, and credentialed CORS restrictions are
> implemented. DAST gates and the full threat model remain follow-up work.

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

Run the equivalent checks locally before opening a PR:

```bash
# Backend
cd backend && bandit -r app && pip-audit --skip-editable
# Frontend
cd frontend && npm audit --audit-level=high
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

## Planned controls (implemented across later phases)

- Argon2id password hashing; JWT access + rotating refresh tokens; RBAC.
- Admin 2FA (TOTP) and full `audit_log`.
- Strict Pydantic input validation; parameterized queries only.
- HSTS at the production TLS edge, finalized with Phase 14 release wiring.
- Redis rate limiting on auth and promo redemption; account lockout/backoff.
- Provider and LLM API keys encrypted at rest; never returned to the client.
- Secrets only from env/secret store; structured logging with secret redaction.
