# Security

> **Status:** scaffolding. This document is filled out fully in the Phase 13
> security-hardening pass with the complete threat model and reproducible
> pentest commands (Semgrep, Bandit, pip-audit / npm audit, Trivy, gitleaks,
> OWASP ZAP baseline, nuclei, and sqlmap notes for the search/backtester
> endpoints). The sections below are the stable outline.

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

## Security headers baseline

Phase 13a adds a shared baseline to API and frontend responses:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `frame-ancestors 'none'` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |

This is intentionally limited to low-risk, response-wide controls. A stricter
nonce-based CSP for scripts/styles remains in the Phase 13 follow-up plan.

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

## Planned controls (implemented across later phases)

- Argon2id password hashing; JWT access + rotating refresh tokens; RBAC.
- Admin 2FA (TOTP) and full `audit_log`.
- Strict Pydantic input validation; parameterized queries only.
- Security headers: response-wide baseline first; nonce-based CSP and HSTS
  follow once production TLS/release wiring lands.
- Redis rate limiting on auth and promo redemption; account lockout/backoff.
- Provider and LLM API keys encrypted at rest; never returned to the client.
- Secrets only from env/secret store; structured logging with secret redaction.
