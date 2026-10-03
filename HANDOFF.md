# BetPulse — Engineering Handoff

Living context document for anyone (human or agent) picking up this project. It captures the
_process rules_, the _current state_, and the _sandbox realities_ that are not obvious from the
code alone. Keep it current: update the "Phase status" table and the "Sandbox / environment
constraints" section whenever they change.

---

## 1. What this is

**BetPulse** — a production-grade football analytics & ML prediction platform. Statistical and
machine-learning predictions (1X2, totals, per-half totals) for live and upcoming matches from six
independent methods plus a calibrated consensus. The full brief is
[`CLAUDE_CODE_BUILD_SPEC.md`](./CLAUDE_CODE_BUILD_SPEC.md); `README.md`, `.env.example` and
[`docs/DATA_SOURCES.md`](./docs/DATA_SOURCES.md) are the supporting canon.

## 2. Non-negotiable process rules

1. **Language.** All code, comments, commit messages, identifiers and docs are in **English**.
   Chat replies to the project owner are in **Russian**.
2. **Phase order.** Build strictly in the §14 phase order (14 phases). **Do not start a phase
   until the previous phase's tests are green in GitHub Actions.**
3. **Plan first.** Before writing code for a phase, post a short plan (files, schema changes,
   tests) and **wait for the owner's explicit "go" on the plan.**
4. **Definition of done (per phase).** Tests written · CI green (all 9 checks) · docs updated
   (incl. `docs/DATA_SOURCES.md` if providers changed) · conventional commit · PR opened.
   **The owner merges PRs. The agent never merges and never pushes to `main`** (except an
   explicitly-authorized direct doc commit like this file).
5. **Each phase on its own branch.** Never push to a different branch without explicit permission.
6. **No shortcuts.** No legacy/deprecated libraries. No secrets in code. No raw SQL string building
   (ORM / bound params only). If the spec is ambiguous, **ask — do not guess.**
7. **Model identity** (`claude-opus-4-8`) must never appear in commits, PRs, code, or any pushed
   artifact — chat replies only.

## 3. Stack & repo layout

Monorepo:

| Path | Contents |
|---|---|
| `/backend` | FastAPI · Python 3.12 · async SQLAlchemy 2.0 · Alembic · Pydantic v2 · ARQ workers |
| `/frontend` | Next.js 15 · React 19 · TypeScript (strict) · Vitest |
| `/infra` | docker-compose · Caddy · custom MLflow image · Postgres init |
| `/.github` | `ci.yml`, `security.yml`, dependabot |
| `/docs` | `DATA_SOURCES.md` (provider canon) |

Data plane: PostgreSQL 16 + TimescaleDB (hypertables `odds`, `predictions_live`) · Redis 7 · MinIO
(S3) · MLflow (Postgres backend + MinIO artifacts) · ARQ (async Redis queues `realtime`, `batch`, `ml`,
one worker service each — see §9k; the spec's five queue names were consolidated).

Backend layout of note: `app/core` (config, db, redis, security, crypto, deps), `app/models`,
`app/providers` (BaseProvider abstraction + football_data_couk + api_football + id_mapping),
`app/services` (auth, ingestion, rate_limit, twofa, audit), `app/ml` (elo, glicko2, dixon_coles,
xg, market, lightgbm_model, consensus, metrics, features, training, evaluation, registry,
mlflow_utils), `app/workers` (arq_app, tasks), `app/api` (health, auth, admin, performance).

## 4. Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Project setup, CI/CD, security scanners, branch protection | ✅ merged |
| 2 | Auth: Argon2id, JWT + rotating refresh (reuse detection), RBAC, admin TOTP 2FA, CSRF, lockout | ✅ merged |
| 3 | Domain model + migrations + provider abstraction + ID mapping + football-data.co.uk ingestion | ✅ merged |
| 4 | 6 ML methods + consensus + calibration + MLflow→MinIO + model registry + `/performance` | ✅ merged |
| 5 | API-Football live ingestion + in-play recompute + SSE streaming + push notifications | ✅ merged |
| 6 | Frontend: design system + match list/card (all method bars + consensus) + light sporty theme + skeletons + i18n RU/EN | ✅ merged |
| 7 | Tiers + feature flags + server-side limit enforcement + guest blur/lock + minimal login | ✅ merged |
| 8 | Promo codes (500-multiple batches, binding, kill-switch, CSV) + redemption + billing seam | ✅ merged |
| 9 | Strategy backtester (filters, matched count, ROI, equity, drawdown, Wilson CI, per-season breakdown, save/export) | ✅ merged |
| 10 | LLM match analysis (OpenAI-compatible, tier-gated by daily rank, token budget + cost, admin config) | ✅ merged |
| 11 | Push (Telegram + Web Push) on probability swings: per-match follow, tier-gated, `pushes_per_day` | ✅ merged |
| 12 | Admin dashboard (sub-PRs 12a–12d). 12a shell+providers+ingestion ✅ · 12b ML management ✅ · 12c spend+users+promo/tiers ✅ · 12d-core system health/audit/test ops alerts ✅ · 12d follow-ups tracked below | 🚧 follow-up hardening pending |
| 13 | Security hardening: headers, rate limits, nonce CSP, strict CORS, Playwright, SQL regressions, manual staging DAST | ✅ complete |
| 14 | 14a release workflow → GHCR → immutable-tag deploy/rollback; production Caddy/Compose ✅ · 14b WAL-G backups/restore drills/ops alerts pending | 🚧 in progress |

## 5. CI — the 9 required checks

Backend (ruff · mypy · pytest), Frontend (eslint · tsc · vitest · build), Docker images build;
plus `security.yml`: gitleaks, bandit, semgrep, pip-audit, npm audit, trivy. Branch protection on
`main` requires all of them green; merging is physically blocked otherwise.

**npm audit gate (production dependencies only).** The blocking step of `Dependency audit (npm)` is
`npm audit --omit=dev --audit-level=high`; a second step runs the full audit (devDependencies
included) with `continue-on-error`, printing it to the job log and the run summary. Why prod-only:
advisory **GHSA-vfj7-8cjw-p6xm** (`braces`, high, stack-exhaustion DoS) reaches us only through the
dev chain `eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces`, and no
fixed `braces` exists (3.0.3 is the latest; `npm audit fix --force` would downgrade
`eslint-config-next` to 14.x). The production image is a Next.js `output: "standalone"` build: only
`.next/standalone` (traced runtime modules), `.next/static` and `public` are copied, so no
devDependency ships — `typescript`, traced only because `next.config.ts` is TypeScript (the server
inlines the compiled config), is excluded via `outputFileTracingExcludes` and guarded by
`frontend/next.config.test.ts`. Anything that does ship is therefore a production dependency and
covered by the blocking gate. **Revisit:** re-enable the full audit as blocking once a fixed
`braces` (or a `fast-glob`/`micromatch` without it) is released — see §11.

Backend CI specifics (`.github/workflows/ci.yml`): Postgres (timescaledb image) + Redis service
containers; installs `libgomp1` for LightGBM; runs the **migration round-trip**
`upgrade head → downgrade base → upgrade head`; runs **offline historical ingestion** against the
committed CSV fixture; then pytest with `--cov-fail-under=80`. MLflow uses a temp **file store** in
CI (`MLFLOW_TRACKING_URI=file://…`, `MLFLOW_ALLOW_FILE_STORE=true`) — Postgres+MinIO only in
dev/prod. `make train` is deliberately **not** in CI (LightGBM on real data takes minutes; the
fixture pipeline test covers the full path instead — see the comment in `ci.yml`).

**Playwright security e2e shipped in Phase 13d.** `Browser security (Playwright)` builds the
production frontend and verifies strict CSP nonces, response headers, a violation-free browser boot,
and locale persistence. Failure artifacts are uploaded for debugging. Component behavior remains
covered by Vitest + React Testing Library.

## 6. Conventions that bite if ignored

- **Migrations** are hand-written in `backend/migrations/versions/` (`0001`–`0004`). Enums via
  `postgresql.ENUM(..., name=...)` created with `checkfirst=True` and `create_type=False` on the
  column; dropped in `downgrade`. Timescale hypertables are guarded by a `pg_available_extensions`
  check so CI (plain PG) and prod (timescaledb) both pass. **Every migration must survive the
  round-trip.**
- **Secrets** (TOTP, provider/LLM keys) are Fernet-encrypted at rest with `DATA_ENCRYPTION_KEY`;
  never returned to the client (masked suffix only). Provider keys are entered in the Admin UI;
  `.env` values are a dev/CI fallback only.
- **Tests** (`backend/tests/conftest.py`) set env **before** importing the app, then an autouse
  fixture truncates **all** `Base.metadata.sorted_tables` between tests (learned the hard way — a
  partial truncate leaked domain rows between tests). Fixtures: `session`, `client` (httpx
  ASGITransport). `asyncio_mode=auto`.
- **Network-dependent tests use recorded fixtures**, never live calls: football-data CSV slice in
  `tests/fixtures/football_data/`, API-Football JSON in `tests/fixtures/api_football/`.
- **Security state is committed before raising.** `get_session()` rolls the request transaction
  back on *any* exception — including the `HTTPException` a router maps a domain error to — so
  anything written on a failure path in the request session is silently lost. Security state that
  must survive a 401/429 (failed-login counter, `locked_until`, token-family revocation, and their
  audit rows) is therefore written via `app.services.auth._commit_security_state()` /
  `app.core.db.independent_transaction()`: a short transaction of its own, committed **before** the
  domain error is raised, which neither commits nor sees the caller's pending changes. Do not
  "fix" this by changing the global rollback semantics. The caller must not hold uncommitted writes
  on rows the security transaction updates (it would wait on its own lock). Counters are bumped
  atomically (`UPDATE … SET n = n + 1 … RETURNING`), never read-modify-write.
- **Refresh rotation is atomic and runs in its own transaction.** `rotate_refresh_token` takes a
  per-family `pg_advisory_xact_lock` (logout takes it too) plus `SELECT … FOR UPDATE` on the token
  row, so exactly one of N concurrent presentations of a token rotates it. A loser whose token's
  direct replacement is still live and was created ≤ `REFRESH_REUSE_GRACE_SECONDS` (default 10, DB
  clock) ago is a benign duplicate (double-click, two tabs): `409 Refresh already in progress`,
  nothing issued, **no cookie changes** (clearing them could wipe the winner's), audit
  `auth.token.refresh_conflict`; the frontend `hydrate()` retries once after 500 ms. Any other
  presentation of a rotated/revoked token revokes the whole family and audits
  `auth.token.reuse_detected`, committed before the 401. Audit rows never carry tokens or hashes;
  failures for unknown/inactive emails store only `email-hmac:<16 hex>` (keyed by `SECRET_KEY`),
  not the address. Every revocation of a user's tokens (logout, password change via
  `revoke_all_user_tokens`, admin `disable_user`) takes the family lock(s) first — all families in
  key order — so a rotation in flight can never leave a live token behind.
- **Redis counters are atomic Lua scripts** (`app/services/counters.py`); never write
  `INCR` + `EXPIRE` (or `INCR` … `DECR`) as separate commands again. `incr_with_ttl` (fixed-window
  rate limits: login/LLM/admin per IP, promo per user; push counting) increments and sets the TTL in
  one step — the un-bucketed `rl:login:ip:*` key without a TTL would be a permanent lockout.
  `incr_within_limit` is check-and-increment against a quota (match views, backtester runs, push
  budget): N concurrent callers can never take more than the limit, and a refused call counts
  nothing. `decr_floor_zero` gives a unit back and never goes below 0. Each script also gives a TTL
  to a key that lost one, so leftovers heal on use. The push daily budget is **reserved before
  delivery** (`reserve_push`) and released if nothing was delivered (`release_push`, also on an
  exception); a crash in between loses one unit — fewer pushes, never more.
- **Database connection budget (two pools per API process).** A failed login holds its request
  connection while it opens the security transaction, so `independent_transaction()` draws from a
  **separate** pool (`DB_SECURITY_POOL_SIZE`=2 + `DB_SECURITY_MAX_OVERFLOW`=3); the request pool is
  `DB_POOL_SIZE`=5 + `DB_MAX_OVERFLOW`=10, and both wait up to `DB_POOL_TIMEOUT_SECONDS`=30. Never
  point security transactions back at the request pool: with one shared pool, N concurrent failed
  logins ≥ pool capacity (15 by default; 1 with a pool of 1) all hold one connection and wait for a
  second until the timeout → 500s and lost lockout state (`test_concurrent_failed_logins_do_not_
  exhaust_the_pool` reproduces it). With two pools there is no cycle: security transactions never
  wait on a request connection, so a saturated security pool only queues. Size the server as
  `Σ_api (DB_POOL_SIZE + DB_MAX_OVERFLOW + DB_SECURITY_POOL_SIZE + DB_SECURITY_MAX_OVERFLOW)
  + Σ_arq_workers (DB_POOL_SIZE + DB_MAX_OVERFLOW) + MLflow server pools + ~10 headroom
  (psql, migrations, backups) ≤ Postgres max_connections − superuser_reserved_connections`.
  The security pool is created lazily, so ARQ workers never open it; a `DATABASE_READ_URL` replica
  pool counts against the replica, not the primary.
  **Current budget** (compose sets `max_connections=120` explicitly; 3 superuser-reserved):

  | Service | Pools | Max connections |
  |---|---|---|
  | api (1 process) | request 5+10, security 2+3 | 20 |
  | worker-realtime (`max_jobs=20`) | 5+10 (defaults) | 15 |
  | worker-batch (`max_jobs=2`) | `DB_POOL_SIZE=2`, `DB_MAX_OVERFLOW=3` | 5 |
  | worker-ml (`max_jobs=1`) | `DB_POOL_SIZE=2`, `DB_MAX_OVERFLOW=3` | 5 |
  | mlflow (`--workers 2`) | (2 workers + the server parent) × 2 stores × (2+3) | 30 |
  | **total** | | **75** of 120 − 3 − 10 headroom = **107** |

  MLflow keeps **one engine per metadata store** (tracking, model registry) in **every** process,
  including the `mlflow server` parent, which is why its share is `(workers + 1) × 2 × (pool +
  overflow)`; the stock defaults (4 workers, 5+10) would need 150 on their own. Measured locally with
  MLflow 3.14 (the image's version), 2 workers, 2+3 pools and 40 concurrent clients: peak 13
  connections (parent 3 idle, each worker ≤ 4). Batch/ml jobs each open exactly one session, so
  their small pools cannot starve. `tests/test_connection_budget.py` recomputes this from the
  compose files (base and base+prod) and the `Settings` defaults, and fails naming the largest
  consumers when the sum exceeds the budget — update it when you add a service, a replica,
  `uvicorn --workers`, or raise a pool. Pool overrides in `.env` are not visible to the guard;
  set per-service values in compose `environment` instead.
- **ML evaluation integrity (ML-A).** Every rating/feature pass walks fixtures through
  `app/ml/chronology.py`: order `(kickoff_at, id)`, and fixtures sharing a kickoff instant form one
  **batch** — all predicted from the pre-batch state, then updated together (Elo: deltas from
  pre-batch ratings; Glicko-2: one rating period per batch; features: rows before updates). Dixon-
  Coles uses running sufficient statistics (goals for/against, matches) read before and updated after
  each batch — the old version fitted strengths on *all* fixtures, its own result included. Tests:
  changing a match's own score, a later result, or a same-kickoff neighbour's result never changes its
  prediction. **Odds** are picked by time (`app/ml/odds_selection.py`): the latest *complete*
  snapshot with `ts <= as_of`; closing = `as_of = kickoff` (inclusive — closing odds are stored at
  `ts = kickoff`), in-play quotes are never used; training (market), evaluation (ROI) and the
  backtester store all use it. **Evaluation** (`compute_rolling_metrics`) scores one explicit
  version per method (default: latest registered) on the fixtures *every* participating method
  predicted; metrics land on that version's registry row. User-facing APIs show only each method's
  current version (`current_registry_rows`), serve each fixture's probabilities from a single
  version, and label every quality figure `prequential_historical` / not verified out-of-sample.
  UI wording (ru, approved): "Индекс Brier, %" (never "Точность"), "ROI по закрывающим
  коэффициентам (ретроспективно)", "… · ретро", plus "прошлые результаты не гарантируют будущих"
  wherever a quality/ROI number appears. `accuracy_pct` is a Brier skill score against the base
  rates of the same window, not accuracy.
- **LightGBM + consensus training (ML-B)** — `app/ml/ml_training.py`, windows cut on kickoff
  instants (`app/ml/splits.py`, never inside a same-kickoff batch, asserted disjoint and ordered):
  - *LightGBM*: train 60 % / validation 20 % (only picks the boosting rounds by early stopping) /
    test 20 %.
  - *Consensus*: bases Elo, Glicko-2, Dixon-Coles (prequential) + LightGBM **out-of-fold** (rolling
    origin over four blocks of the first 60 %: each block predicted by a LightGBM trained on the
    earlier blocks only). Meta-model on those OOF rows, isotonic calibration on 60-80 % (never seen by
    the meta-model; probabilities floored at 1 %), metrics on 80-100 %, which no stage was fitted on.
    The LightGBM feeding each window is trained only on rows before it. `xg` has no predictor and is
    not an input.
  - Only test-window fixtures get predictions, so every stored LightGBM/consensus probability is
    out-of-sample. MLflow logs the test metrics (`test_brier`, `test_log_loss`, `test_hit_rate`,
    `test_skill_pct` vs *training* base rates) and the window boundaries; the registry
    `sample_count` is the test size. Too little data → an explicit reason in
    `TrainingSummary.skipped` (`insufficient samples (n < 200)`, `test window too small (k < 40
    matches)`, …) — 40 / 60 / 20 are technical floors, not quality bars.
  - **Data volume.** With today's ~400 matches both methods train, but their test window is ~80
    matches, far below `CHAMPION_MIN_SAMPLES` (300 common matches), so they **cannot become
    champion — intended**. On a synthetic 396-match season LightGBM scored a +4.6 % Brier index on its
    test window and consensus −3.9 % (worse than base rates): stacking + calibration needs more data.
    Importing multi-season history is the real prerequisite for useful ML models.
- **Champion rule** (`apply_champion_selection`, ML-B). Inputs come from one evaluation that scored
  every method on the same fixtures. Eligible = enabled, all metrics finite (a failed evaluation never
  competes and is stored as NULL, not NaN), common `sample_count >= CHAMPION_MIN_SAMPLES` (300).
  Best = lowest **Brier**, then log loss (`accuracy_pct` is display-only). A current eligible champion
  is replaced only if the best is at least `CHAMPION_MIN_BRIER_IMPROVEMENT` (0.002) lower; with no
  champion — or one not eligible in this evaluation — the best eligible becomes champion; with no
  eligible method nothing changes. Each rule has a test.
- **Data-quality report (read-only, safe on prod):** `make data-report REPORT_ARGS="[--leagues
  EPL,LALIGA] [--seasons 2025-2026] [--json] [--strict]"` (`app/services/data_quality.py`). Per
  league/season: fixtures vs expected `n×(n−1)`, teams, % with score, % with a known kickoff time
  (until HI-2 adds `kickoff_time_known`: a 00:00 UTC kickoff counts as date-only), closing 1X2
  coverage **per bookmaker** and O/U 2.5 coverage. **Errors** (exit 1): the same pairing in a league
  within ±36 h under any season label (cross-source duplicate, found even when the partner sits
  outside `--seasons`), finished without score, impossible score (<0, >15, HT>FT), finished with a
  future kickoff, a price ≤ 1.0 or > 1000, a *closing* quote dated after kickoff. **Warnings** (exit 1
  only with `--strict`): incomplete odds snapshot, 1X2 overround outside [1.00, 1.25], non-canonical
  season label, team count not 16/18/20, and — only for seasons with no fixture in 60 days —
  missing fixtures / uneven team schedules, plus closing-1X2 coverage below 90 %. Run it before any
  data migration to see conflicts upfront.
- **Idempotency everywhere.** Ingestion upserts use `ON CONFLICT DO NOTHING` on identity keys
  (`uq_fixture_identity`, odds identity, prediction identity). Tasks keyed by
  `fixture_id + method + model_version` so retries/duplicate deliveries are safe.

## 7. Sandbox / environment constraints (important)

The agent's build/verify environment is **not** the production environment. Known limits:

- **Docker registry egress is blocked** (cloudfront blob pulls return 403; the agent proxy is
  unreachable from inside containers). `docker compose pull`/multi-stage builds that fetch base
  images fail here. Local verification instead runs **Postgres 16 and Redis as host processes**:
  system Postgres at `/usr/lib/postgresql/16/bin` (run as the `postgres` user, data dir
  `/tmp/bp_pgdata`, port 5432, socket `/tmp`) and system `redis-server` on 6379.
- **`football-data.co.uk` is blocked by the proxy policy (403)**, so the committed CSV fixture is a
  documented, format-faithful reconstruction (real EPL 2023-24 matchday-1 scores + representative
  odds), flagged in the Phase 3 PR — not a live download.
- **MLflow 3** refuses a file store unless `MLFLOW_ALLOW_FILE_STORE=true` (set in tests/CI).
- **Outbound HTTPS** goes through an agent proxy (CA bundle `/root/.ccr/ca-bundle.crt`); on
  403/405/407/TLS failures see `/root/.ccr/README.md`. Never disable TLS verification.

## 8. Dependency pins that are load-bearing

`backend/pyproject.toml` caps some versions to keep the tree consistent and pip-audit clean:
`redis==5.3.1` (capped by `arq<6`), `cryptography==50.0.2` (capped by `mlflow==3.16.1`, which
requires `cryptography<51`) and `pandas==2.3.3` (mlflow 3.16 allows `pandas<4`; kept on 2.x
deliberately). Bumping cryptography past 50.x requires an mlflow release that lifts that cap.
`tests/test_dependency_known_answers.py` pins a Fernet token and a JWT issued under the previous
cryptography/PyJWT versions, so a bump that breaks data at rest or live sessions fails CI. Ruff selects `E,F,I,UP,B,ASYNC,S`; mypy strict + `pydantic.mypy` with
`ignore_missing_imports` for pandas/joblib/lightgbm/statsmodels/sklearn. Do not bump these blindly;
re-run `pip-audit --skip-editable` after any change.

## 9. Phase 5 plan (in progress)

Branch `claude/live-phase-5`, off merged `main`. Scope & design (owner-approved defaults):

1. **Live ingestion** — ARQ task on queue `live` (now `realtime`, §9k), self-rescheduling every `LIVE_POLL_INTERVAL_SECONDS`
   (default 60) under a Redis single-flight lock; polls `/fixtures?live=all`; parses → upserts
   fixtures + fixture_stats; writes `predictions_live`; **hard-stops on quota**; idempotent.
2. **In-play recompute** — separate ARQ task (queue `live`, now `realtime`), triggered after each successful poll
   (not a fixed timer); Dixon-Coles conditioned on current score + elapsed minute, then LightGBM
   with live features; recompute **only if state changed**; on swing > `PROBABILITY_SWING_PUSH_THRESHOLD`
   (default 0.10) vs the previous `predictions_live` row → enqueue a push job (queue `push`, now `realtime`).
3. **Transport — SSE** (not WebSocket): `GET /live/stream`, auth-gated by tier (guest/free cannot
   stream); one event per fixture update; on reconnect (`Last-Event-ID`) replay from an append-only
   `live_updates` log, ≤ 5 minutes; Redis pub/sub fan-out between API replicas.
4. **Push** (queue `push`, now `realtime`) — Telegram via `TELEGRAM_BOT_TOKEN` (Bot HTTP API on httpx) + Web Push
   via VAPID; skip if no `push_subscriptions` row; on failure log+discard, one retry after 30s;
   rate limit ≤ 1 push per (user, fixture) per 5 minutes in Redis.
5. **Migration `0005_live_push`** — `push_subscriptions`, `live_updates` (BIGSERIAL id =
   `Last-Event-ID`), `users.tier` enum (free/pro/expert, default free). `predictions_live` already
   exists.
6. **Tests** — live poll idempotency; recompute skipped when unchanged; swing enqueues/doesn't; SSE
   `Last-Event-ID` replay + guest/free 403; push rate limit; Redis fan-out (replica A → replica B).
   No live API key in CI — recorded cassette; `ci.yml` comment: "live polling tested against
   recorded fixture; real key on VPS."

Owner-confirmed design decisions: add `users.tier` now as the SSE gating seam; use append-only
`live_updates` for the monotonic `Last-Event-ID` (the Timescale `predictions_live` composite PK has
no monotonic id); Telegram via Bot HTTP API (not aiogram); self-rescheduling ARQ task under a Redis
lock; unmapped API-Football team/league during live → structured warning + skip the fixture
(resolver stays strict; alias seeding is an admin task, seeded in tests).

## 9b. Phase 7 notes (tiers + enforcement)

- **Tiers are data with a code fallback.** `tiers` rows (`feature_flags` + `limits` JSONB) are the
  source of truth; `app/services/tiers.py::DEFAULT_TIERS` seeds them (migration `0007` + idempotent
  `seed_default_tiers`) and is also the fallback when a row is missing, so resolution never fails
  closed. Resolved tiers are cached in Redis 60s; admin `PATCH /admin/tiers/{id}` invalidates the
  cache so edits land on the next request. **Tests use the code fallback** (they don't run the
  migration), so tier logic works without seeding; admin tests call `seed_default_tiers` explicitly.
- **Effective tier** = most-privileged active (non-expired) subscription → else `users.tier` → else
  `guest`. `guest` is a real tier row (the unauthenticated baseline).
- **Method-bar gating is server-side.** `GET /matches/{id}` returns per-method bars only for
  pro/expert (`flags.methods` in `all`/`all_weights`); guest/free get `methods: []` + `flags` so the
  frontend renders the blur/lock. Aggregate signals (consensus, agreement %, delta) are shown to all.
- **matches/day** counts `GET /matches/{id}` per caller (user id, or the guest's client IP from
  `app.core.deps.get_client_ip`, IPv6 bucketed per /64). Redis key `limits:{id}:{YYYY-MM-DD}`, TTL = seconds to next **UTC midnight**
  (not rolling 24h). Over budget → `403 {tier_required}` (guest→free, free→pro). The list is free and
  reports `matches_remaining`.
- **SSE gating** now reads the same `live_recompute` flag (was the `UserTier.can_stream_live` enum).
- **Frontend auth is minimal** (spec allows): access token in a Zustand store (memory only), refresh
  token in the backend's httpOnly cookie. `/api/auth/*` route handlers proxy to the backend and relay
  Set-Cookie; the proxy rewrites the refresh cookie `Path=/auth/refresh` → `/` so logout/refresh work
  same-origin. No registration form — test with a seeded/bootstrapped account. Silent refresh on
  mount restores the session after reload; a `409` (another tab rotated the token at the same time)
  is retried once after 500 ms with the re-read CSRF cookie. A rejected refresh (401: missing,
  unknown/expired or reused token) is *returned* as a `JSONResponse` that deletes both auth cookies —
  raising `HTTPException` would drop the injected `Response`'s Set-Cookie headers.
- **Billing seam**: `app/services/billing.py::PaymentProvider` (abstract, no impl); `subscriptions.
  source = payment` reserved. Promo codes are Phase 8.

## 9c. Phase 8 notes (promo codes)

- **Codes never stored in plaintext.** Only an HMAC-SHA256 ``code_hash`` (keyed by
  ``DATA_ENCRYPTION_KEY`` — no new secret) is persisted; the plaintext is returned **once** at
  generation (`POST /admin/promo/batches` → `codes` + `warning: plaintext_codes_shown_once`). The
  later `export.csv` is **metadata only** (code_id, status, activations_used, bound_user_id,
  created_at) — plaintext can't be re-derived. Batch size must be a multiple of 500.
- **Double-spend is impossible.** Redemption claims a slot with one guarded statement —
  `UPDATE promo_codes SET activations_used = activations_used + 1 WHERE id=:id AND status='active'
  AND activations_used < max_activations RETURNING …` — and 409s when it affects no row. No
  read-then-write. Verified by a concurrent-redemption test (`asyncio.gather`, one 200 + one 409).
  `max_activations` is denormalised onto `promo_codes` so this stays a single-row update.
- **Redemption effects** (`POST /promo/redeem`): `trial` → subscription with `expires_at = now +
  value days`; `upgrade` → subscription (perpetual or batch expiry) — both `status=applied`. `percent`/
  `fixed` → a `promo_redemptions` row with `status=pending` (the billing seam reads it at checkout;
  no subscription). The response's `effect: {type, value, status}` drives the frontend message.
- **Kill-switch** is atomic: `UPDATE promo_codes SET status='disabled' WHERE batch_id=:id` (+ the
  batch row), one statement, not a loop.
- **Rate limit**: per-user, per-hour, key `rate_limit:promo:{user_id}:{YYYY-MM-DD-HH}` → 429 +
  `Retry-After`. Hash comparisons use `hmac.compare_digest`, never `==`.
- **Frontend**: a "Redeem" popover in the header (signed-in) posts through `/api/promo/redeem`;
  on success it invalidates the match queries so a trial/upgrade unblurs the card immediately.
- Admin generation is API-only this phase; the admin UI lands in Phase 12. (Note: hitting the admin
  promo endpoints over real HTTP needs an admin who passed 2FA; tests mint the admin token directly.)

## 9d. Phase 9 notes (backtester)

- **Precomputed feature store.** `backtest_features` (migration `0009`) holds one indexed row per
  finished fixture — as-of Elo / rolling xG / rest days / form (reused from
  `app.ml.features.build_feature_table`, chronological, no leakage) plus a closing-odds snapshot
  (1X2 + over/under 2.5). Populated by `app.services.backtester.population.populate_backtest_features`
  (idempotent upsert by `fixture_id`). Filters hit this table, not runtime joins.
- **Totals odds added.** The football-data provider now also parses Pinnacle closing over/under 2.5
  (`PC>2.5`/`PC<2.5`, fallback `P>2.5`/`P<2.5`) → `odds` market `ou_2.5` (over/under). The committed
  E0 fixture gained those two columns; the Phase-3 odds-count assertions moved 30 → 50.
- **Bet types** are **1X2 and total over/under 2.5** — the only markets with stored closing odds.
  The run response lists `available_bet_types` for the filtered dataset (first-half totals and
  handicap are **not** implemented — no data — and are intentionally absent, not stubbed).
- **SQL-injection safe.** Every filter is a whitelisted, typed field turned into an ORM
  bound-parameter comparison — never string-interpolated. Verified by a test that runs a real query
  with a SQL fragment in a string filter and asserts a literal (0-row) match, table intact.
- **Metrics**: matched count, win-rate, ROI on closing odds, equity curve, max drawdown, Wilson 95%
  CI (`{lower, upper, confidence}`, formula in a code comment), per-league/season breakdown.
  `roi_disclaimer: true` is always present; `small_sample_warning: true` when matched < 100 (the
  frontend renders a yellow card above results). Every result carries `evaluation_protocol =
  "historical_rule_simulation"` and `odds_basis = "closing"`: a fixed rule replayed over past matches,
  nothing trained. `?season_split=true` adds `season_splits` — every season, oldest first, the first
  one included — which is a **breakdown, not walk-forward**: the former `walk_forward` /
  `out_of_sample_roi` / `folds` were removed because no model is re-fitted per season, so no part of
  the result is out-of-sample. The UI says so under every result (`backtester.protocolNote`).
- **Tiering**: runs consume `backtester_runs_per_day` (guest 0 / free 3 / pro 50 / expert ∞) via a
  Redis UTC-day counter; **save** and **export** are the `backtester_save`/`backtester_export` feature
  flags (expert only). Migration `0009` also patches the flags onto the tier rows seeded by `0007`.
  CSV export carries no internal UUIDs (date, teams, league, season, bet, odds, outcome, P/L, cum P/L).
- **Filter validation** (`StrategyFilter`): decimal odds `1.0 < odds ≤ ODDS_UPPER_BOUND` (1000), no
  NaN/inf in any numeric filter, and every `*_min`/`*_max` pair (odds, elo_diff, avg_total) must be
  ordered → 422 before the run quota is touched. A strategy saved under the older, looser rules
  answers **422** ("save the strategy again") on export instead of a 500. The form's odds inputs
  carry matching `min`/`max`/`step` hints; the server stays the authority.

## 9e. Phase 10 notes (LLM analysis)

- **Purpose, not source of truth.** The LLM *explains* the model outputs in plain language — it is
  never the source of the probabilities. `not_a_probability_source: true` is a **top-level** field on
  the analysis response (not buried in the text), and the frontend renders a disclaimer under every
  narrative regardless of what the model wrote.
- **Provider config is a singleton.** `llm_config` (migration `0010`) is one admin-managed row: any
  OpenAI-compatible `base_url` + `model`, an API key **encrypted at rest** (Fernet, same as provider
  keys — only a masked `••••1234` suffix is ever returned, the full key is never logged), plus
  `max_tokens`, `daily_token_budget`, `cache_ttl_seconds`, `cost_per_1k_in/out`, `is_enabled`
  (default off). Kept deliberately separate from `provider_accounts` (data providers) — different
  concern. Admin: `GET/PATCH /admin/llm-config` (RBAC + audit `llm_config.update`, secret value never
  in the audit meta — only the field names).
- **Tier gate is a DB lookup, not a runtime computation.** An ARQ cron (`rank_llm_fixtures_task`,
  midnight UTC) ranks today's scheduled fixtures by `model_agreement_pct × |edge_vs_market|`
  (confidence × edge) and writes `fixtures.fixture_llm_rank` (1 = match of the day; null = not ranked
  today). The `llm` feature flag gates on that rank: guest `none` → 403; free `match_of_day` → rank 1;
  pro `top5` → ranks 1–5; expert `any` → any fixture. Migration `0010` patches the `llm` flag onto the
  tier rows seeded by `0007`.
- **Cache + budget + cost.** Analyses are cached per `(fixture_id, model)` (unique constraint); a
  cached row older than `cache_ttl_seconds` is regenerated, never served stale (`cached: true/false`
  on the response). A per-UTC-day Redis counter `llm:budget:{YYYY-MM-DD}` hard-stops generation once
  `daily_token_budget` is spent → `{"status": "budget_exhausted", "resets_at": "<UTC midnight ISO>"}`.
  Both token counts **and** computed cost (`cost_per_1k_*`) are stored on `llm_analyses` for the
  admin spend dashboard (Phase 12). Token/cost are **not** exposed on the public response.
- **Prompt is English-only** (spec §8); the response language is a request param (`?language=ru|en`,
  driven by the user's locale) appended as "Respond in {language}." — nothing hard-coded to Russian.
  `generate_completion` is the only function that touches the network, isolated so tests monkeypatch
  it (no live key needed).
- **Frontend**: `AnalysisBlock` on the match detail page renders the narrative + always-on
  disclaimer, a match-of-the-day badge, a tier lock/CTA on 403, and a "resets at HH:MM" message on
  `budget_exhausted`; hidden entirely when disabled/no-data. Same-origin proxy
  `GET /api/matches/[id]/analysis` forwards the bearer + locale.

## 9f. Phase 11 notes (push on probability swings)

- **Builds on the Phase 5 delivery core** (VAPID JWT, `send_telegram`/`send_webpush`, one-retry
  dispatch). Phase 11 makes it a real product: per-match targeting, tier gating, a daily budget, and
  the frontend.
- **Per-match follow, not broadcast.** `push_follows` (migration `0011`, unique `(user, fixture)`)
  records who follows a fixture; `dispatch_push` now joins subscriptions to **followers of that
  fixture** and delivers to nobody else. `PUT/DELETE /live/push/follow/{id}` + `GET
  /live/push/follows` drive the "notify me" toggle.
- **Push is Pro/Expert only.** `pushes_per_day` becomes the gate: guest 0 / **free 0** / pro 10 /
  expert ∞. Migration `0011` patches the free tier row (was 1). `ResolvedTier.can_receive_push()`
  (`pushes_per_day != 0`) guards subscribe / follow / Telegram-link via the shared `require_push_tier`
  dependency. `DEFAULT_TIERS` free updated to match.
- **Daily budget hard-stop, count deliveries.** A per-UTC-day Redis counter
  (`limits:push:{user}:{day}`) is *peeked before* delivery (never overspend) and *incremented only on
  a successful* delivery — a failed push does not consume the budget. The per-(user, fixture) window
  rate-limit from Phase 5 is unchanged.
- **Web Push = tickle + fetch (no payload crypto).** The push body is the fixture id; the service
  worker (`frontend/public/sw.js`) fetches the public `GET /live/push/latest/{id}` snapshot and
  renders the notification, then opens `/matches/{id}` on click. RFC 8291 payload encryption is
  intentionally avoided.
- **Dead endpoints are pruned.** A Web Push `404/410` raises `PushGone`; `dispatch_push` deletes that
  subscription row so it is not retried forever.
- **Telegram deep-link.** `telegram_link_tokens` (SHA-256 hash only, single-use `used_at`, 15-min
  expiry — the DB row is the sole source of truth, no Redis copy). `POST /push/telegram/link` mints
  `t.me/<bot>?start=<token>` (Pro/Expert); Telegram's `/start` hits `POST /push/telegram/webhook`,
  authenticated by `X-Telegram-Bot-Api-Secret-Token` compared with `hmac.compare_digest`. A
  missing/wrong secret is logged and answered **200 OK (empty)** so Telegram never retries; only a
  valid `/start <token>` records the chat id as a Telegram `PushSubscription`. `DELETE /push/telegram`
  disconnects.
- **Frontend.** `NotifyToggle` on the match detail (tier-locked chip for guest/free, follow/unfollow
  otherwise, flips to a lock on a 403); `/settings` → Notifications (enable/disable browser push,
  connect/disconnect Telegram). Same-origin proxies under `/api/push/*` and `/api/live/push/*`.
  RU/EN. New settings: `telegram_bot_username`, `telegram_webhook_secret`.

## 9g. Phase 12 notes (admin dashboard)

Phase 12 is delivered as **four sub-PRs**, each its own branch + diff summary, merged in order:
**12a** providers + ingestion log (+ the admin shell), **12b** ML management, **12c** spend + users +
promo/tier UI, **12d** system health + audit viewer + ops alerts.

### 12a (providers + ingestion + admin shell)

- **Admin shell** at `/admin/*` (`app/admin/layout.tsx`): sidebar nav + a **client** RBAC guard that
  waits for auth to hydrate (new `hydrated` flag on the auth store) then bounces non-admins to `/`.
  The backend enforces RBAC regardless (`require_admin`); this is only the UX guard. An "Admin" link
  shows in the header for admins.
- **Providers**: full CRUD + enable/disable over `provider_accounts` at `/admin/providers`
  (`app/api/providers.py`, `services/providers.py`). Admin-only, every mutation audited. The API key
  is write-only — only a masked `••••1234` suffix is returned, never the plaintext/ciphertext.
- **Ingestion log**: new `ingestion_runs` table (migration `0012`) — one row per (provider, league,
  season) run with status/counts/duration/error. `services/ingestion/runner.run_recorded_ingestion`
  writes them (one row per pair, each pair isolated in a savepoint so one failure doesn't abort the
  batch). `GET /admin/ingestion/runs` (paginated + status filter) drives the job log.
- **Re-scan**: `POST /admin/ingestion/rescan` validates leagues against `LEAGUE_META`, then enqueues
  the new `ingest_history_task` ARQ job (historical football-data only; live polling runs on its own
  schedule). The API enqueues via a per-request ARQ pool (`app/core/arq.get_arq_pool`).
- **Progress = polling, not SSE.** The ingestion page polls `/admin/ingestion/runs` on a configurable
  interval (default 5s) and **stops automatically** once no run has `status=running`
  (`nextPollInterval` helper → React Query `refetchInterval`). It never polls forever.

### 12b (ML model management, spec §16)

- **Builds on existing governance** in `app/ml/registry.py` — `snapshot_registry` /
  `rollback_to_snapshot` (atomic full-state restore), `_softmax_weights`, `apply_champion_selection`.
  12b exposes it via `/admin/models` + a Models page; it does not rebuild the pipeline.
- **Runtime weighting mode.** New singleton `model_weighting` (migration `0013`, `mode` auto|manual),
  admin-editable — replaces the env `consensus_weight_mode` at the nightly re-eval, which now reads
  the persisted mode (so **manual** weights survive the nightly run). `services/model_admin.py` holds
  the orchestration. **auto** = softmax of `accuracy_pct` over visible methods (sum 100); **manual** =
  admin weights, validated **sum = 100** (409 if not in manual mode, 422 if the sum is off). Flipping
  back to auto **recomputes + persists softmax immediately** (owner requirement — no waiting for the
  cron).
- **Governance actions** (all admin-only, audited, snapshot-first where relevant): `PATCH
  /admin/models/{id}` (enabled/visible/notes), `PUT /weighting`, `PUT /weights`, `POST
  /{id}/promote|demote`, `GET /snapshots` + `GET /snapshots/{id}/diff` (rollback **diff preview**:
  status/weight before→after) + `POST /rollback/{id}`, `POST /retrain` (enqueues `train_all_task`).
- **Manual promote below threshold is allowed but traceable**: the response carries
  `{"promoted": true, "warning": "below_min_samples"}` and the audit meta records `{"override": true}`.
- **Weight unit note.** `display_weight` is a **percentage** (softmax already sums to 100); it is
  surfaced for display/governance (matches card for expert, admin table) and is **not** read by the
  live consensus math — changing it is safe. Frontend Models page: metrics table, enabled/visible
  toggles, weight inputs (editable only in manual, Save gated to sum 100), mode toggle, promote/demote,
  retrain, snapshots list + rollback with diff preview.

### 12c (LLM spend + user management + promo/tier UI)

- **No migration** — every table already exists (`llm_analyses`, `subscriptions`, `promo_batches`,
  `refresh_tokens`, `tiers`). All new endpoints are admin-only (`require_admin`) and every mutation is
  audited.
- **LLM spend** (`GET /admin/llm/spend?days=N`, `services/llm/spend.py`): daily token/cost buckets
  aggregated in SQL with an **explicit UTC anchor** — `date_trunc('day', created_at AT TIME ZONE
  'UTC')` — so day boundaries are deterministic regardless of server timezone (tests seed explicit UTC
  timestamps, never `datetime.now()`). Plus the **top-20 fixtures by cost** (team/league labelled) and
  the current `daily_token_budget`. `days` is validated **1..90 (422 outside)** — no arbitrarily large
  windows. The Spend page (Recharts bar chart of daily tokens with a budget reference line + per-fixture
  table) also embeds the **LLM config editor** over the existing `/admin/llm-config` (the API key stays
  write-only / masked-suffix).
- **User management** (`/admin/users`, `services/user_admin.py`): list with **email search + effective-
  tier filter + pagination** — the effective tier (most-privileged active subscription, else base
  `users.tier`) is resolved **in SQL** (window-function subquery) so the filter and page counts stay
  consistent. `POST /{id}/tier` grants a tier by creating a **`source=manual` subscription** (upsert on
  `uq_subscription_user_tier`, optional `expires_at`) — it **never touches `users.tier`**. `GET
  /{id}/redemptions` lists a user's promo history. `POST /{id}/disable` sets `is_active=False` **and
  revokes every one of the user's refresh tokens (`revoked=True`) in the same transaction** — the
  15-minute access-token window is too long for a security disable; `POST /{id}/enable` reactivates.
- **Promo UI** (`/admin/promo`) over the existing 12a-era `/admin/promo/batches` endpoints: batch list +
  kill-switch + a full generate form (name, code_type, size, value, tier, max_activations, expires_at,
  stackable, optional bind-to-user). Client validation mirrors the server: **size multiple of 500**;
  the **value field is hidden for `upgrade` codes** (value unused); the **bound-user field only appears
  when "bind to user" is checked**. Plaintext codes are shown **once** after generation and offered as a
  client-side CSV download — they are never stored or re-fetchable.
- **Tiers UI** (`/admin/tiers`) over `/admin/tiers`: edit price / is_public / `feature_flags` /
  `limits` (JSON editors with parse validation) per tier; the backend PATCH invalidates the resolved-
  tier cache so edits take effect within seconds.
- **12d-core shipped** (`GET /admin/system/health`, `services/system_health.py`): admin-only health
  summary for Postgres, Redis and Telegram ops-alert configuration. Hard dependency failures return
  an overall `error`; optional/unconfigured alerting returns `degraded` so deploy smoke checks can
  distinguish broken core services from missing optional notifications.
- **Audit viewer shipped** (`GET /admin/audit`): admin-only paginated audit log with action, actor,
  target, date-range and free-text filters. The response includes actor email for operator usability
  while keeping raw `meta` structured for debugging. Pagination must remain stable (`created_at DESC,
  id DESC`) so page boundaries do not jump when timestamps tie.
- **Ops-alert smoke shipped** (`POST /admin/system/alerts/test`, `services/ops_alerts.py`):
  admin-triggered Telegram test alert using the existing `TELEGRAM_ALERT_CHAT_ID` env name; not
  configured returns a non-fatal `not_configured` response and successful sends are audited as
  `ops_alert.test`. Telegram HTTP/transport failures are controlled delivery errors, not raw 500s.

### 12d follow-up backlog (owner plan, keep as small PRs)

The owner-approved fuller Phase 12d scope is larger than the 12d-core PR. Do not silently fold all of
this into an unrelated phase; schedule it as one or more follow-up PRs after 12d-core is merged and CI
is green:

1. **Extended system health + real readiness** — add components for API readiness, ARQ worker/queue
   depth, latest ingestion run, latest model re-evaluation, today's LLM token spend, and backup status
   (`not_configured` until Phase 14 backup lands). Upgrade `/health/ready` from the process-only stub
   to real Postgres + Redis checks.
2. **Automatic ops alerts with Redis dedup** — send Telegram ops alerts when ingestion fails or the
   LLM daily budget is exhausted. Deduplicate via Redis keys to avoid alert spam. No new table: write
   alert attempts/results to `audit_log`.
3. **Frontend health expansion** — extend the System page to show the new components and queue/last-run
   metadata with clear `ok`/`degraded`/`error`/`not_configured` states.
4. **Tests** — backend tests for readiness success/failure, ARQ/queue metadata, latest-run metadata,
   LLM spend-today, backup `not_configured`, and ops-alert dedup/audit behavior; frontend tests for the
   expanded System page.

## 9h. Phase 13 plan (security hardening)

Phase 13 is the pre-production security pass. Keep it reviewable: prefer several focused PRs over one
large diff, and do not proceed while any required security/CI job is red.

- **13a — response headers (merged):** shared CSP frame protection, permissions policy,
  referrer policy, content-type protection, and clickjacking headers. Production HSTS remains at
  the Caddy TLS edge and is finalized with Phase 14 release wiring.
- **13b — sensitive rate limits (merged):** Redis-backed limits for login, promo redemption, LLM
  analysis, and unsafe admin mutations.
- **13c — browser security (merged):** per-request nonce CSP for rendered Next.js routes,
  explicit credentialed CORS methods/headers/origins, production wildcard rejection, and regression
  tests. The existing cookie-based root layout is already dynamic, matching the nonce requirement.
- **13d — security-test gates (implemented):** required Playwright browser-security CI; bound-value
  SQL-injection regressions for matches, backtester and audit filters; and a manual, authorization-
  gated staging workflow for pinned OWASP ZAP, Nuclei and conservative sqlmap scans. DAST remains
  manual until Phase 14 provides a stable staging URL.
- **Secret hygiene:** verify secrets are never logged or returned, and keep provider/LLM keys
  write-only/masked.
- **SQL safety review:** verify query surfaces use ORM/bound parameters only; add regression tests for
  user-controlled filters in audit, backtester and search-like endpoints.
- **Dependency/security scanners:** keep Semgrep, Bandit, pip-audit, npm audit, Trivy and gitleaks green;
  add OWASP ZAP, nuclei and sqlmap runs/docs where feasible without making PR CI prohibitively slow
  (heavy DAST may be nightly/manual if documented).
- **Docs:** add `SECURITY.md` with exact reproduction commands, scope, expected outputs, false-positive
  handling, and how CI blocks merges.
- **Client IP:** `app.core.deps.get_client_ip` is the only
  client-IP source (login/admin limits, promo, audit, guest identity). It honours
  `X-Forwarded-For` only from `TRUSTED_PROXY_CIDRS` (right-most untrusted hop, IP-validated) and
  uvicorn runs with `--no-proxy-headers`. Compose pins `web`/`caddy` addresses
  (`BETPULSE_NETWORK_SUBNET`, `BETPULSE_WEB_IP`, `BETPULSE_CADDY_IP`) and trusts exactly those /32s;
  production refuses to start without an explicit, private, narrow list. Rate-limit and guest-quota
  keys bucket IPv6 by /64; audit keeps the full address. On the frontend every `app/api` route
  handler reaches FastAPI only through `lib/server/backendProxy.ts` (directly or via `authProxy`),
  which forwards the bearer token and the right-most valid `X-Forwarded-For` hop (the Caddy-set
  client IP; `X-Real-IP` is never read). `lib/server/routeHandlers.test.ts` statically fails any
  handler that calls `fetch` or reads the backend URL itself.

## 9i. Phase 14 plan (release workflow, deploy and backups)

Phase 14 turns the green main branch into a deployable release. The owner plan supersedes the old
Makefile comments that labelled backup/restore as a later track; update those targets when Phase 14 is
implemented.

- **Release workflow:** add `.github/workflows/release.yml` to run required tests, build Docker images,
  push backend/frontend images to GHCR, and tag images with both the release version and `latest`.
- **Production deploy:** implement `make deploy` on the server: pull GHCR images, run `alembic upgrade
  head`, restart with Docker Compose, run health checks, and rollback if the health check fails.
- **Rollback/ops commands:** add `make rollback`, production `make logs`/diagnostic helpers, and docs for
  staging/production rollback.
- **Production infra:** finalize `docker-compose.prod.yml` resource limits and Caddy reverse-proxy/TLS
  configuration with security headers.
- **Backups:** add WAL-G continuous Postgres archiving to S3-compatible storage, `make backup`, weekly
  `make restore-drill`, backup freshness checks, and Telegram ops alerting when backups are stale
  (owner target: alert if backup is older than 15 minutes).
- **Docs:** update README/deploy docs with required env vars, tag-based release flow, deploy, rollback,
  backup and restore-drill commands.

### Post-deploy manual checklist

Run after the **first** deploy of a new image (and after any change to the frontend build/trace
config) — CI only proves the image builds (`Docker images build`), not that it serves:

- [ ] From the real frontend image: `GET /api/health`, `GET /` and `GET /performance` all respond
  `200` (the standalone trace excludes `typescript`; a missing runtime module would surface here).

## 9j. Legal & compliance (Russian Federation)

The target jurisdiction is the Russian Federation. Russian legal texts are authoritative; English is a
courtesy translation.

**Implemented (spec §19):**
- Five pages under `frontend/app/legal/`: terms (Пользовательское соглашение), privacy (Политика
  обработки персональных данных, 152-FZ art. 18.1), consent (a **standalone** consent to personal data
  processing, never embedded in the terms), responsible, disclaimer. Texts live in
  `frontend/content/legal/{ru,en}.ts` (server-rendered, not shipped in the client message bundle);
  operator details and retention periods are `[PLACEHOLDERS]` in `frontend/config/legal.ts` only. A
  DRAFT banner (`LEGAL_DRAFT`) stays on every page until a lawyer specialising in Russian personal-data
  law has reviewed the texts. Keep the privacy policy in sync with the code: `content/legal/legal.test.ts`
  fails if a cookie the app sets is missing from it.
- Footer links all five pages; the 18+ age gate links to them and is not shown on `/legal/*`; it
  re-prompts when `bp_age_ok` expires (`AGE_GATE_CONSENT_DAYS`, passed to the `web` container).
- Disclaimers: full §19 text in the footer, on the match page and under backtester results; a short form
  on every match card; "past performance does not predict future results" next to every ROI figure.

**Mandatory before launch (requirements, not yet code):**
- **Data localisation:** the production database holding personal data of RF citizens must be hosted in
  the RF (152-FZ art. 18 part 5). This also applies to backups (Phase 14b WAL-G target) — decide the
  hosting and backup locations during deploy planning.
- **Roskomnadzor notifications** — operator registration (art. 22) and cross-border transfer notice
  (art. 12: Telegram, browser push services) — are the operator's manual tasks; fill the registry number
  into `config/legal.ts` afterwards.
- **Retention and deletion (152-FZ art. 21):** there is currently no automatic purge of `audit_log`,
  expired tokens or other records and no account-deletion flow. Implement the retention/deletion
  procedure (and set the real periods in `config/legal.ts`) before launch.
- **Registration UI:** when a sign-up form is built it must include an **unchecked** consent checkbox
  linking to `/legal/consent` (plus links to the terms and privacy policy); registration must not proceed
  without it.
- **Product rule:** no bookmaker affiliate links, odds-to-bookmaker deep links or calls to place bets
  without legal review (gambling regulation and advertising law).
- **Cookie/consent banner** (PR-2 of this work): analytics only after consent through
  `hasConsent("analytics")`; no analytics provider is integrated.

## 9k. Background workers (ARQ queues)

`app/workers/queues.py` is the single source of truth: queue names plus the task → queue mapping,
and `enqueue(pool, task, ...)` is the only way code enqueues (a test fails on any other `enqueue_job`
call or queue literal in `app/`). `app/workers/arq_app.py` has one settings class per queue, each run
as its own Compose service:

| Queue / service | Tasks | Cron | Limits |
|---|---|---|---|
| `realtime` / `worker-realtime` | `poll_live_task`, `recompute_fixture_task`, `push_task` (+ live-loop bootstrap on startup) | — | 20 jobs, 60 s; poll and push `max_tries=1` |
| `batch` / `worker-batch` | `ingest_history_task`, `rank_llm_fixtures_task` | LLM ranking 00:00 UTC | 2 jobs, 30 min |
| `ml` / `worker-ml` | `train_all_task`, `reevaluate_champions_task` | champion re-eval 04:00 UTC | 1 job, 2 h |

- **Deviation from the spec.** §18 lists five queues (`ingest`, `train`, `live`, `push`, `llm`). ARQ
  serves one queue per worker process, so five queues would mean five processes, each importing the ML
  stack. The goal of §18 is isolation of training from live recomputation; three queues achieve it.
  Splitting a task out later (e.g. `push`) is one mapping entry plus one settings class.
- **Why `ml` is a separate process, not just a queue:** training is CPU-bound and runs on the event
  loop, so it stalls everything else in its process (including ARQ's heartbeat — hence the lenient
  `worker-ml` healthcheck). In-play recompute is ~20 µs of Dixon-Coles maths per fixture plus DB I/O,
  so it stays on the realtime event loop without a thread pool.
- **Healthchecks:** `python -m app.workers.healthcheck <queue>` checks the worker's ARQ heartbeat key
  in Redis (cheap; `arq --check` would import the ML stack on every probe).
- **Rollout of the split:** jobs left in ARQ's old default queue (`arq:queue`) at deploy time are not
  consumed by the new workers. The deferred live poll is harmless (`worker-realtime` re-bootstraps the
  loop); admin-triggered re-scans or retrains that were queued but not started must be re-triggered
  from the admin UI. Inspect/drop leftovers with `redis-cli ZRANGE arq:queue 0 -1` /
  `redis-cli DEL arq:queue`. `deploy.sh` runs `up -d --remove-orphans`, which stops the old single
  `worker` container. Rolling back to a pre-split image also needs the pre-split Compose files.
- **Scaling:** run `worker-ml` on a dedicated host by starting only that service there (same image and
  env, same Redis/Postgres/S3) and stopping it on the main host.

**Backlog (separate tasks):**
- **Nightly retrain cron.** `RETRAIN_CRON` (`.env`) is read nowhere; the only ML cron is the champion
  re-evaluation, hard-coded to 04:00. Wire `RETRAIN_CRON` to a `train_all_task` cron on `MlWorker`
  (and decide whether re-evaluation follows training).
- **Live-chain deduplication.** `poll_live_task` re-schedules itself in `finally`, and every
  `worker-realtime` start enqueues a new poll. Two chains can therefore coexist: after a restart (the
  old deferred job survives in Redis) or with two realtime replicas. The Redis single-flight lock
  stops *concurrent* polls, and a chain whose tick finds the lock held ends (the early return skips
  the re-schedule), but chains whose ticks never overlap — offset by more than one poll duration —
  both survive and poll at up to twice the configured rate, burning API-Football quota. Fix: give the
  re-scheduled job a fixed `_job_id` so ARQ refuses duplicates, which requires `keep_result=0` on
  `poll_live_task` (a kept result blocks re-use of the id for `keep_result` seconds) and enqueueing the
  next tick only after the current job's key is released (a job cannot re-enqueue its own id while it
  is still running) — e.g. via an `after_job_end` hook or a lock-guarded scheduler. Needs a careful
  test against real ARQ semantics.

## 10. How to resume

1. Read this file + the spec §14 for the current phase.
2. `git fetch origin` and check whether the current phase branch's PR has merged. If merged and
   there is follow-up work, restart the branch from fresh `origin/main` (do not stack on merged
   history).
3. Confirm the previous phase is green in CI before starting the next one.
4. Post the phase plan, wait for "go", then implement → tests → CI green → PR. The owner merges.

## 11. Parked work (owner-requested, not yet scheduled)

- **Re-enable the full blocking npm audit** once GHSA-vfj7-8cjw-p6xm (`braces`, dev-only via
  `eslint-config-next`) has a fixed release: make the "all dependencies" step in `security.yml`
  blocking again (drop `continue-on-error`) and delete the prod-only note in §5.

- **ML follow-ups** (out of scope of the ML honesty PRs):
  - likelihood-fitted Dixon-Coles with time decay (and shrinkage toward the league mean) replacing
    the running-mean estimator;
  - a real forward test (predictions frozen before kickoff, scored after) before any metric is
    presented as verified;
  - drift monitoring; dataset snapshots per training run; feature-store restructuring;
  - an xG predictor (the `xg` method has no training path; it is not a consensus input);
  - a **paired bootstrap** on per-match Brier differences (champion vs challenger on the same
    fixtures) as a significance condition for champion changes — the 0.002 absolute margin is below
    the noise level at ~300 matches;
  - multi-season history import, the prerequisite for LightGBM/consensus to reach champion size.

- **Auth hardening follow-ups** (found during the auth-transactions PR; separate small PRs):
  - *Lockout as a victim-DoS vector.* The per-account backoff (`LOGIN_MAX_FAILURES`, then
    `LOCKOUT_BASE_SECONDS·2ⁿ` capped at `LOCKOUT_MAX_SECONDS`) is keyed on the account only, so
    anyone who knows an email can keep that user locked out. Consider a shorter cap, keying the
    backoff on account + client bucket, or a challenge (CAPTCHA/email link) instead of a hard lock.
  - Still open: backtester memory (M-02); LLM issues (H-02, L-01), including the LLM daily token
    budget in `app/services/llm/analysis.py`, which still does a non-atomic `INCRBY` + `EXPIREAT`
    (dated key, so a leak rather than a lockout) — move it onto `app.services.counters` with the
    LLM PR.

- **Custom user alerts** (a *separate* phase, to be scheduled **after Phase 14 release/deploy**). User-defined alert
  rules that trigger a push delivery to Telegram / Web Push when their condition is met on a live
  fixture. Conditions combine: **minute**, **score**, **probability threshold**, **probability
  swing**, and **edge vs market**. Per-tier caps: **Pro up to 5 alerts, Expert up to 50**. Builds on
  the Phase 11 push-delivery + per-match-follow plumbing (reuse `dispatch_push`, the `pushes_per_day`
  counter, and the subscription channels). Not in scope for Phase 11 — recorded here so it is not
  forgotten.
- **Cookie/consent banner** (postponed by the owner; was PR-2 of the legal work). Agreed design:
  - first-party `bp_consent` cookie (necessary vs analytics) read **on the server** in the root layout, so
    the banner is server-rendered with no hydration flicker and needs no inline script (nonce CSP);
  - non-modal bottom panel rendered **under** the 18+ age-gate overlay; actions **accept all**,
    **reject** (necessary only — the choice is stored too) and **manage** (analytics toggle + save);
  - 180-day lifetime plus a consent **version**: an expired cookie or an older version re-prompts;
  - a "Cookie settings" button in the footer reopens the panel;
  - a `hasConsent("analytics")` helper (client and server variants) that any future analytics code
    must go through; **no analytics provider** is added with the banner;
  - the Privacy page cookie section must then list `bp_consent` (the policy/cookie test enforces it).
  - Russian-law note: a cookie banner is not strictly mandatory in the RF while the site sets only
    necessary cookies; it becomes relevant as soon as an analytics provider (or any non-essential
    cookie) is added, so build it together with — or before — the first analytics integration.
