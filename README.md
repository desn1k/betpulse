# Football Analytics & ML Prediction Platform

Statistical and machine-learning predictions for **live and upcoming football matches**:
1X2, match totals, and per-half totals — from six independent methods plus a calibrated consensus.

> **Disclaimer / Дисклеймер.** Analytical and informational purposes only. Predictions are
> statistical estimates, guarantee nothing, and are **not** gambling advice or a financial
> recommendation. **18+.**
> Информация носит исключительно аналитический характер, не гарантирует результат и не является
> призывом к участию в азартных играх. **18+.**

---

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 (App Router) · React 19 · TypeScript · Tailwind v4 · shadcn/ui · TanStack Query · next-intl (RU/EN) |
| Backend | Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic |
| Data | PostgreSQL 16 (+TimescaleDB) · Redis 7 · S3-compatible object storage |
| Jobs | ARQ workers + scheduler (queues: `ingest`, `train`, `live`, `push`, `llm`) |
| ML | numpy · scipy · statsmodels · scikit-learn · LightGBM · MLflow |
| LLM | Any OpenAI-compatible endpoint (configurable `base_url`) |
| Infra | Docker Compose · Caddy (auto-TLS) · GHCR · GitHub Actions |

## Prediction methods

| Method | What it gives |
|---|---|
| Elo (football-adapted) | Interpretable baseline rating |
| Glicko-2 | Rating + deviation (confidence) |
| Dixon-Coles (bivariate Poisson) | Full score matrix → 1X2, any total, per-half totals |
| Own xG model | Shot-based xG / rolling xG-xGA form |
| LightGBM | Multiclass 1X2 + goals regression |
| Market-implied (de-vig) | Benchmark: the line to beat |
| **Consensus** | Stacked + isotonically calibrated blend |

Rolling 90-day **Brier / log-loss / ROI-vs-closing-line** are published per method (public
`GET /performance`, served from `model_registry` — not recomputed per request). The method with
the highest accuracy is auto-promoted to **champion** by a nightly ARQ job; admins can override
weights manually.

### Public match feed

The frontend match list and match card read two public (no-auth) endpoints:

- `GET /matches` — upcoming/live fixtures that have at least one prediction. Filters: `league`
  (code), `status` (`scheduled|live|finished`), `date` (`YYYY-MM-DD`); `limit`/`offset`
  pagination (default 20, max 50). Default window is today plus the next two days,
  scheduled + live. Each item carries the calibrated consensus, the champion method and its
  accuracy %, and a `data_delayed` flag (set when a live fixture's last poll is older than
  five minutes — a user-facing signal of stalled polling / provider quota exhaustion).
- `GET /matches/{id}` — the full card: per-method 1X2 bars for `is_visible` methods, the
  consensus, `model_agreement_pct` (how tightly the methods cluster on the home-win
  probability), and `delta_vs_market` (consensus minus market-implied home probability, null
  when no odds exist).

### Tiers & enforcement (spec §7)

Tiers are **data**, not hardcode: the `tiers` table holds `feature_flags` + `limits` (JSONB) that
drive server-side authorization and the frontend blur/lock. An admin edits them at runtime
(`GET`/`PATCH /admin/tiers` — 60s Redis cache, invalidated on write); the code-defined defaults
seed the rows and act as a fallback. A caller's effective tier is their most-privileged active
subscription, else their base `users.tier`, else `guest`.

- **Method bars** are returned by `GET /matches/{id}` only for pro/expert; guest/free get an empty
  `methods` list plus a `flags` object (`{methods, per_half_totals, live_recompute}`) so the
  frontend blurs/locks. Enforcement is server-side; the frontend only mirrors flags.
- **Daily match limit**: `GET /matches/{id}` counts one view per caller (user id, or guest IP from
  the first `X-Forwarded-For`) in Redis, keyed by UTC date and reset at UTC midnight. Over the tier
  budget → `403 {tier_required}`. `GET /matches` is free and reports `matches_remaining`.
- **Auth (minimal)**: email+password login returns a short-lived JWT (held in browser memory) and
  sets a rotating refresh token in an httpOnly cookie. The billing layer (`PaymentProvider`) is
  stubbed so online payments plug in later without a schema change; monetization today is manual
  grants and (Phase 8) promo codes.

### Training & model governance

```bash
make train      # build features → train → log to MLflow → write predictions → upsert model_registry
```

Each training run stores its **model binary**, **feature schema** (JSON), **training-data hash**
(sha256 of the input DataFrame) and **metrics** in MLflow. MLflow keeps tracking metadata in a
dedicated `mlflow` Postgres database and writes **artifacts to MinIO/S3** (`S3_BUCKET_ARTIFACTS`) —
never to local disk, so any past model version can be rolled back (§16/§17). A nightly
`reevaluate_champions` ARQ task recomputes rolling out-of-sample accuracy and promotes the champion,
snapshotting the full registry first for one-click rollback.

> LightGBM and the consensus stack need a real dataset; on tiny inputs (e.g. CI's fixture) the
> pipeline **skips** them with a logged note and trains Elo / Glicko-2 / Dixon-Coles / market. Run
> the full `make train` on the VPS after `make bootstrap-history`.

---

## Quick start (local)

```bash
git clone <repo> && cd <repo>
cp .env.example .env          # fill in the secrets — see comments in the file
make up                       # docker compose up -d (postgres, redis, minio, api, worker, beat, web, mlflow)
make migrate                  # alembic upgrade head
make seed                     # tiers, leagues, admin user
make bootstrap-history        # ingest football-data.co.uk CSVs (free, no key needed)
make train                    # train all methods, register in MLflow
```

Open http://localhost:3000 · API docs http://localhost:8000/docs · MLflow http://localhost:5000

Then log in as admin → **Providers** → add your API-Football key, assign roles (`live`, `odds`),
set daily/minute limits.

## Commands

```bash
make up / make down / make logs
make migrate            # alembic upgrade head
make test               # backend pytest + frontend vitest + playwright
make lint               # ruff + mypy + eslint + tsc
make security           # semgrep, bandit, pip-audit, npm audit, trivy, gitleaks
make train              # retrain all enabled methods
make backup             # on-demand encrypted DB + artifact backup
make restore-drill      # restore latest backup into a throwaway container and verify
make deploy             # pull GHCR images on the VPS, migrate, up -d, health-check
```

---

## Production deployment (single VPS)

Recommended: 4–8 vCPU / 16 GB RAM / NVMe. Everything runs in Docker Compose behind Caddy (auto-TLS).

```bash
# on the server
git clone <repo> && cd <repo>
cp .env.example .env && $EDITOR .env      # production secrets
make deploy
```

`make deploy` pulls the images published to GHCR by `release.yml`, runs migrations, restarts the
stack, and fails if the health check does not go green.

### Scaling to multiple servers

The design is stateless-by-default, so scaling out requires no rewrite:

- `api` — run N replicas behind the proxy; Redis pub/sub fans out live updates between them.
- `worker` — run N workers, optionally on a dedicated CPU-heavy host; point it at the same
  Redis + Postgres + S3. Split queues so training never starves live recomputation.
- `beat` — exactly **one** active scheduler; extra instances hold a Redis lock as hot standby.
- Postgres — primary + read replica (read/write routing is already in the session factory).
- Object storage — S3-compatible from day one (MinIO locally → any provider in prod).

All tasks are idempotent (`fixture_id + method + model_version`), so retries and duplicate
deliveries are safe.

---

## Backups & disaster recovery

Three tracks, all **encrypted before leaving the host**:

1. **Database** — WAL-G continuous archiving to S3: nightly base backup + WAL shipping → PITR.
   Retention 7 daily / 4 weekly / 6 monthly.
2. **Models** — MLflow artifacts on S3. Every version keeps the model binary, feature schema,
   training-data hash, metrics, and a `model_registry` snapshot → **rollback to any past version
   without retraining**. Champions are kept forever; last 10 versions per method otherwise.
3. **Config/secrets** — repo is the source of truth; secrets backed up separately, encrypted.

**A backup that has never been restored is not a backup.** `make restore-drill` runs weekly in CI:
it restores the newest backup into a disposable container and diffs the schema. Failure pages you
via Telegram.

**Targets:** RPO ≤ 15 min · RTO ≤ 1 h.

### Recovery runbook

```bash
# 1. provision a clean host, clone repo, restore .env from the secret store
# 2. bring up postgres only
docker compose up -d postgres
# 3. restore to the latest consistent point (or a timestamp)
make restore                       # or: make restore PITR="2026-07-12 18:30:00+03"
# 4. verify
make restore-verify                # row counts + schema diff + latest fixture sanity check
# 5. bring up the rest, models are pulled from S3 automatically
make up && make deploy
```

---

## Security

See [`SECURITY.md`](./SECURITY.md) for the full threat model and the exact pentest commands
(Semgrep, Bandit, pip-audit / npm audit, Trivy, gitleaks, OWASP ZAP, nuclei, sqlmap).

Highlights: Argon2id, JWT + rotating refresh, RBAC, admin TOTP 2FA, nonce-based CSP, HSTS,
Redis rate limiting, parameterized queries only, provider/LLM API keys **encrypted at rest and
never returned to the client**, full audit log.

Report vulnerabilities privately — do not open a public issue.

### Authentication

Email + password accounts with:

- **Argon2id** password hashing (parameters documented in `backend/app/core/security.py`).
- **Access token** (short-lived JWT) returned in the JSON body and kept in memory by the client;
  **refresh token** delivered only as an `HttpOnly` + `SameSite=Strict` cookie scoped to
  `/auth/refresh`, with **double-submit CSRF** on refresh/logout.
- **Rotating refresh tokens** with family-wide **reuse detection** (a replayed token revokes the
  whole session family).
- Login abuse controls: **per-IP** rate limit **and** per-account **exponential-backoff** lockout
  (never permanent); responses do not reveal whether an email exists.
- **RBAC** (`guest` / `user` / `admin`); admin routes require a changed bootstrap password **and**
  enabled **TOTP 2FA**.
- `require_verified` email gate (feature-flagged via `EMAIL_VERIFICATION_REQUIRED`, default off).
- Every security event — successes **and** failures — is written to `audit_log`.

Routes live under `/auth` (`register`, `login`, `refresh`, `logout`, `me`, `change-password`,
`verify-email`, `2fa/{setup,enable,disable}`). Create the first admin with:

```bash
make seed            # python -m app.bootstrap create-admin
```

If `ADMIN_PASSWORD` is empty a strong one-time password is printed once; the admin must change it
on first login before admin features unlock.

---

## Contributing / CI gate

`ci.yml` (lint, type-check, pytest ≥ 80 % coverage, vitest, Playwright) and `security.yml` must be
**green** before any merge. Branch protection enforces this; there are no exceptions.
