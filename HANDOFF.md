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
| 9 | Strategy backtester (filters, matched count, ROI, equity, drawdown, Wilson CI, walk-forward, save/export) | ✅ merged |
| 10 | LLM match analysis (OpenAI-compatible, tier-gated by daily rank, token budget + cost, admin config) | ✅ merged |
| 11 | Push (Telegram + Web Push) on probability swings: per-match follow, tier-gated, `pushes_per_day` | ✅ merged |
| 12 | Admin dashboard (sub-PRs 12a–12d). 12a shell+providers+ingestion ✅ · 12b ML management ✅ · 12c spend+users+promo/tiers | 🚧 in progress (12c on `claude/admin-users-phase-12c`; 12d next) |
| 13–16 | (per §14) | ⬜ not started |

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

## 10. How to resume

1. Read this file + the spec §14 for the current phase.
2. `git fetch origin` and check whether the current phase branch's PR has merged. If merged and
   there is follow-up work, restart the branch from fresh `origin/main` (do not stack on merged
   history).
3. Confirm the previous phase is green in CI before starting the next one.
4. Post the phase plan, wait for "go", then implement → tests → CI green → PR. The owner merges.

## 11. Parked work (owner-requested, not yet scheduled)

- **Custom user alerts** (a *separate* phase, to be scheduled **after Phase 12**). User-defined alert
  rules that trigger a push delivery to Telegram / Web Push when their condition is met on a live
  fixture. Conditions combine: **minute**, **score**, **probability threshold**, **probability
  swing**, and **edge vs market**. Per-tier caps: **Pro up to 5 alerts, Expert up to 50**. Builds on
  the Phase 11 push-delivery + per-match-follow plumbing (reuse `dispatch_push`, the `pushes_per_day`
  counter, and the subscription channels). Not in scope for Phase 11 — recorded here so it is not
  forgotten.
