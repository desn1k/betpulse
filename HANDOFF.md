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
2. **Phase order.** Build strictly in the §14 phase order (16 phases). **Do not start a phase
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
(S3) · MLflow (Postgres backend + MinIO artifacts) · ARQ (async Redis queue/scheduler; queues
`ingest`, `train`, `live`, `push`, `llm`).

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
| 9 | Strategy backtester (filters, matched count, ROI, equity, drawdown, Wilson CI, walk-forward, save/export) | 🚧 in progress (`claude/backtester-phase-9`) |
| 10–16 | (per §14) | ⬜ not started |

## 5. CI — the 9 required checks

Backend (ruff · mypy · pytest), Frontend (eslint · tsc · vitest · build), Docker images build;
plus `security.yml`: gitleaks, bandit, semgrep, pip-audit, npm audit, trivy. Branch protection on
`main` requires all of them green; merging is physically blocked otherwise.

Backend CI specifics (`.github/workflows/ci.yml`): Postgres (timescaledb image) + Redis service
containers; installs `libgomp1` for LightGBM; runs the **migration round-trip**
`upgrade head → downgrade base → upgrade head`; runs **offline historical ingestion** against the
committed CSV fixture; then pytest with `--cov-fail-under=80`. MLflow uses a temp **file store** in
CI (`MLFLOW_TRACKING_URI=file://…`, `MLFLOW_ALLOW_FILE_STORE=true`) — Postgres+MinIO only in
dev/prod. `make train` is deliberately **not** in CI (LightGBM on real data takes minutes; the
fixture pipeline test covers the full path instead — see the comment in `ci.yml`).

**Playwright e2e is deferred** — add it in the Phase 13 security hardening pass or as a standalone
task. Phase 6's frontend is covered by Vitest + React Testing Library (component, i18n, age-gate,
disclaimer, language-switcher); the CI frontend job stays `eslint · tsc · vitest · build`.

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
`redis==5.3.1` (capped by `arq<6`), `pandas==2.3.3` and `cryptography==48.0.1` (capped by
`mlflow<49`). Ruff selects `E,F,I,UP,B,ASYNC,S`; mypy strict + `pydantic.mypy` with
`ignore_missing_imports` for pandas/joblib/lightgbm/statsmodels/sklearn. Do not bump these blindly;
re-run `pip-audit --skip-editable` after any change.

## 9. Phase 5 plan (in progress)

Branch `claude/live-phase-5`, off merged `main`. Scope & design (owner-approved defaults):

1. **Live ingestion** — ARQ task on queue `live`, self-rescheduling every `LIVE_POLL_INTERVAL_SECONDS`
   (default 60) under a Redis single-flight lock; polls `/fixtures?live=all`; parses → upserts
   fixtures + fixture_stats; writes `predictions_live`; **hard-stops on quota**; idempotent.
2. **In-play recompute** — separate ARQ task (queue `live`), triggered after each successful poll
   (not a fixed timer); Dixon-Coles conditioned on current score + elapsed minute, then LightGBM
   with live features; recompute **only if state changed**; on swing > `PROBABILITY_SWING_PUSH_THRESHOLD`
   (default 0.10) vs the previous `predictions_live` row → enqueue a push job (queue `push`).
3. **Transport — SSE** (not WebSocket): `GET /live/stream`, auth-gated by tier (guest/free cannot
   stream); one event per fixture update; on reconnect (`Last-Event-ID`) replay from an append-only
   `live_updates` log, ≤ 5 minutes; Redis pub/sub fan-out between API replicas.
4. **Push** (queue `push`) — Telegram via `TELEGRAM_BOT_TOKEN` (Bot HTTP API on httpx) + Web Push
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
- **matches/day** counts `GET /matches/{id}` per caller (user id, or guest IP from the first
  `X-Forwarded-For`). Redis key `limits:{id}:{YYYY-MM-DD}`, TTL = seconds to next **UTC midnight**
  (not rolling 24h). Over budget → `403 {tier_required}` (guest→free, free→pro). The list is free and
  reports `matches_remaining`.
- **SSE gating** now reads the same `live_recompute` flag (was the `UserTier.can_stream_live` enum).
- **Frontend auth is minimal** (spec allows): access token in a Zustand store (memory only), refresh
  token in the backend's httpOnly cookie. `/api/auth/*` route handlers proxy to the backend and relay
  Set-Cookie; the proxy rewrites the refresh cookie `Path=/auth/refresh` → `/` so logout/refresh work
  same-origin. No registration form — test with a seeded/bootstrapped account. Silent refresh on
  mount restores the session after reload.
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
  frontend renders a yellow card above results). Walk-forward (`?walk_forward=true`) splits by season
  chronologically — the first season is warm-up, later seasons are out-of-sample test folds, and
  `out_of_sample_roi` aggregates them (in-sample never includes future seasons).
- **Tiering**: runs consume `backtester_runs_per_day` (guest 0 / free 3 / pro 50 / expert ∞) via a
  Redis UTC-day counter; **save** and **export** are the `backtester_save`/`backtester_export` feature
  flags (expert only). Migration `0009` also patches the flags onto the tier rows seeded by `0007`.
  CSV export carries no internal UUIDs (date, teams, league, season, bet, odds, outcome, P/L, cum P/L).

## 10. How to resume

1. Read this file + the spec §14 for the current phase.
2. `git fetch origin` and check whether the current phase branch's PR has merged. If merged and
   there is follow-up work, restart the branch from fresh `origin/main` (do not stack on merged
   history).
3. Confirm the previous phase is green in CI before starting the next one.
4. Post the phase plan, wait for "go", then implement → tests → CI green → PR. The owner merges.
