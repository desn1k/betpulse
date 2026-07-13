# Build Spec — Football Analytics & ML Prediction Platform

> **How to use this file:** Paste the whole document into Claude Code as the project brief.
> Build in the phase order given. Do not move to the next phase until the current phase's
> tests are green in GitHub Actions. Respond to me in Russian in chat, but keep **all code,
> comments, commit messages, identifiers, and docs in English**.

---

## 0. Product summary

A cross-platform web app that shows **ML-driven predictions** for **live and upcoming football
matches**: 1X2 (win/draw/loss), match total, and per-half totals. Multiple independent analytical
methods are displayed side by side, plus a calibrated **consensus**. Admins connect one or more
football-data providers via the UI (add API key, set limits, watch ingestion & training). Guests
see limited data; registered/paid tiers unlock more. Monetization is promo-code based for now
(online payments planned later). A strategy backtester lets users define match filters and see how
many historical matches match and how a bet would have performed. Top matches get an extra
LLM-written narrative analysis.

**Non-negotiables:** production-ready, secure, optimized, scalable, readable. Modern 2026 stack, no
legacy libraries. Light theme, energetic sporty design (green/lime accent, large cards), loading
indicators everywhere. Full GitHub automation with mandatory green tests before merge.

---

## 1. Confirmed decisions (do not re-ask)

| Topic | Decision |
|---|---|
| League coverage at launch | Top-5 Europe (EPL, La Liga, Serie A, Bundesliga, Ligue 1) + UEFA (UCL/UEL/UECL) + RPL |
| Live mode | Yes — in-play probability recomputation every 1 min + push on sharp probability swing |
| Push channels | Telegram bot (aiogram already exists on my side) **and** Web Push (browser) |
| LLM provider | Any OpenAI-compatible endpoint (configurable `base_url`, model, key in admin) |
| Auth | Email + password |
| Payments | Promo codes only for now; design the billing layer so online payment plugs in later |
| Tiers | Guest / Free / Pro / Expert — all fields editable by admin (see §7) |
| Promo batches | Generated in multiples of 500 codes (500/1000/1500…) |
| UI languages | Russian + English (i18n from scratch, RU default) |
| ML methods | Elo, Glicko-2, Dixon-Coles, own xG model, LightGBM, Market-implied + calibrated Consensus |
| GitHub automation | CI (tests + security scans) + build Docker images to GHCR + one-command deploy |
| Design direction | Sporty energetic, light theme, green/lime accent, large cards |
| Hosting target | Single VPS, everything in Docker Compose (ML worker separable later) |

---

## 2. Data provider strategy

Two data planes; **do not** try to serve both from one source.

1. **Historical plane (ML training + backtester):** `football-data.co.uk` free CSVs — 30+ seasons,
   HT/FT results **and closing odds from 10+ bookmakers** (Pinnacle, Bet365…). This is the bootstrap
   that makes ROI/CLV computable. Coverage ≈ major European leagues (maps cleanly to our launch set
   except RPL — for RPL fall back to the live provider's history).
2. **Live + upcoming + odds plane:** `API-Football` (api-sports.io). 1200+ leagues, 15-second live
   updates. All endpoints on every plan; only request volume differs. Pro tier (7,500 req/day) is
   enough for our coverage (live endpoint returns all in-play fixtures in one call → ~1,400 req/day
   at 1-min polling).

**xG caveat:** API-Football xG is inconsistent per league/season. Build **our own shot-based xG**
model as the source of truth; treat any provider xG as an optional secondary feature.

### Provider abstraction (mandatory)

```
BaseProvider (abstract)
  ├─ capabilities: {historical, live, odds, xg}
  ├─ fetch_fixtures(date_range) -> list[FixtureDTO]
  ├─ fetch_live() -> list[LiveFixtureDTO]
  ├─ fetch_odds(fixture_id) -> OddsDTO
  ├─ fetch_stats(fixture_id) -> StatsDTO
  └─ rate_limit_state() -> QuotaDTO
Implementations: ApiFootballProvider, FootballDataCoUkProvider, (extensible: Sportmonks, TheOddsAPI)
```

- Admin assigns each connected provider one or more **roles** (`historical|live|odds|xg`) and a
  **priority**. Ingestion picks highest-priority provider that has the needed role and remaining quota.
- **ID mapping layer:** canonical internal `team_id` / `league_id`; per-provider alias tables so the
  same team from two sources resolves to one entity. Deduplicate fixtures across providers.
- Per-provider limits: requests/min, requests/day, hard stop on quota (never overspend).
- All provider API keys stored **encrypted at rest** (envelope encryption, app-level KMS key from
  env/secret); keys are **write-only from the UI** — never returned to the client after saving,
  only a masked suffix is shown.

---

## 3. Tech stack (2026, no legacy)

**Frontend**
- Next.js 15 (App Router, RSC, streaming) + React 19 + TypeScript (strict)
- Tailwind CSS v4 + shadcn/ui (Radix primitives)
- TanStack Query (server state), Zustand (light client state)
- Recharts or visx for probability bars / equity curves
- `next-intl` for i18n (RU/EN), RU default
- Skeleton loaders + optimistic UI + suspense boundaries everywhere

**Backend**
- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic migrations
- PostgreSQL 16 (+ TimescaleDB extension for live probability time-series & odds movement)
- Redis 7 (cache, rate-limit buckets, pub/sub for live fan-out)
- ARQ (async Redis queue) for workers + scheduler (ingestion, training, in-play recompute, pushes)
- WebSocket (FastAPI) / SSE for live probability streaming to the browser

**ML**
- numpy, pandas, scipy, scikit-learn, LightGBM, statsmodels
- MLflow (experiment tracking, model registry, versioning, rollback)
- Custom implementations for Elo / Glicko-2 / Dixon-Coles / xG (documented, unit-tested)

**LLM**
- `openai` SDK pointed at a configurable OpenAI-compatible `base_url`

**Infra**
- Docker + Docker Compose; Caddy (automatic TLS) as reverse proxy
- Services: `web` (Next.js), `api` (FastAPI), `worker` (ARQ), `beat` (scheduler), `postgres`,
  `redis`, `caddy`, `mlflow`
- GHCR for images; deploy via `make deploy` (pull images + `docker compose up -d` + migrate)

> If any generated frontend cannot meet the 2026 design bar, isolate the design system into a
> standalone package first (tokens + components) and iterate there; do **not** ship a mediocre UI.

---

## 4. Domain data model (core tables)

- `leagues`, `teams`, `provider_team_aliases`, `provider_league_aliases`
- `fixtures` (status: scheduled|live|finished, ht/ft scores, minute)
- `fixture_stats` (shots, shots_on_target, possession, xg_provider, corners…)
- `shots` (x, y, type, outcome) — feeds own xG
- `odds` (bookmaker, market, price, timestamp) — time-series
- `ratings_elo`, `ratings_glicko` (per team, per date)
- `predictions` (fixture_id, method, market, outcome, probability, model_version, created_at)
- `predictions_live` (Timescale hypertable: fixture_id, minute, method, probabilities)
- `model_runs` (mlflow_run_id, method, metrics: brier/logloss/roi, status, created_at)
- `users`, `sessions`, `refresh_tokens`
- `tiers` (name, price, period, feature_flags JSONB, limits JSONB, is_public)
- `subscriptions` (user_id, tier_id, source: promo|manual|payment, expires_at)
- `promo_batches` (size multiple-of-500, type, value, tier_id, per-user binding, max_activations,
  expires_at, status)
- `promo_codes` (batch_id, code_hash, bound_user_id?, activations_used, status)
- `strategies` (user_id, filters JSONB, saved)
- `push_subscriptions` (user_id, channel: telegram|webpush, endpoint/keys)
- `provider_accounts` (name, roles, priority, encrypted_key, quota state)
- `audit_log` (actor, action, target, ip, ts)
- `llm_analyses` (fixture_id, provider, model, tokens_in/out, cost, content, created_at)

All money/probability fields typed precisely (Decimal / float with documented bounds).

---

## 5. ML layer (methods + consensus)

Each method is a class implementing a common interface and produces probabilities for the same
markets so they can be displayed and ensembled uniformly:

```
class BaseModel:
    def predict_1x2(fixture) -> {home, draw, away}
    def predict_totals(fixture, lines) -> {line: {over, under}}
    def predict_half_totals(fixture, half, lines) -> ...
    def score_matrix(fixture) -> np.ndarray  # where applicable
```

1. **Elo (football-adapted):** goal-difference-weighted updates, home advantage term. Interpretable
   baseline. Persist rating history.
2. **Glicko-2:** carries a rating deviation → exposes confidence (promoted teams / post-break teams
   get wider intervals). Surface RD in the UI.
3. **Dixon-Coles (bivariate Poisson):** attack/defence strengths + low-score correlation + time
   decay. Produces the full **score matrix** → derive 1X2, any total, per-half totals, handicaps.
   This is the totals engine.
4. **Own xG model:** shot-based xG from `shots` (logistic on distance/angle/type), rolling xG/xGA
   over N matches with regression to the mean. Feeds features to LightGBM.
5. **LightGBM:** multiclass 1X2 + separate goals regression. Features: Elo, Glicko (+RD), rolling
   xG/xGA, form, rest days, home/away, motivation flags, line movement. Time-series-safe CV
   (no leakage), early stopping.
6. **Market-implied (benchmark, not a model):** de-vig with Shin / power method (not naive 1/k).
   Shown as the line to beat; report our edge vs it.

**Consensus:** stacking meta-model (logistic regression over methods 1–5) → **isotonic calibration**.
Match card shows all method bars + consensus, plus "model agreement %" and "delta vs market".

**Public quality metrics (trust surface):** rolling 90-day Brier, log-loss, and ROI-vs-closing-line
per method. Store in `model_runs`; render on a public "Model performance" page.

**Live/in-play:** every 1 min for live fixtures, recompute using current score + minute + live stats
(Dixon-Coles conditioned on elapsed time; LightGBM with live features). Write to `predictions_live`.
On probability swing above admin-set threshold, enqueue push jobs.

**Overfitting guardrails:** walk-forward / out-of-sample season holdout; warn on small samples.

---

## 6. Strategy backtester

Filter builder: league, season, home/away, odds range, Elo delta, rolling xG form (N matches),
avg team total, rest days, table position, motivation (top-6/relegation). Bet type: 1X2 / total /
first-half total / handicap.

Output: **matched-match count**, win-rate, ROI on closing odds, equity curve, max drawdown,
per-league/season breakdown, Wilson confidence interval. Warn when sample < 100. Offer walk-forward
validation on out-of-sample years. Save strategies; export CSV (tier-gated).

Implementation: precomputed feature store + parameterized SQL/pandas filter; **never** interpolate
user filter values into raw SQL — use bound params / a whitelisted query builder.

---

## 7. Tiers, feature flags, monetization

Tiers are **data, not hardcode**. `tiers.feature_flags` + `tiers.limits` (JSONB) drive both API
authorization and frontend blur/lock rendering.

Default seed (admin-editable name/price/period/limits/visibility):

| Feature | Guest | Free | Pro | Expert |
|---|---|---|---|---|
| Matches/day | 3 | 10 | ∞ | ∞ |
| Methods shown | consensus (blurred) | consensus | all 6 | all 6 + weights |
| Per-half totals | ✕ | ✕ | ✓ | ✓ |
| Live recompute | ✕ | ✕ | ✓ | ✓ |
| Pushes | ✕ | 1 match | 10 | ∞ |
| Backtester | ✕ | 3 runs/day | 50 | ∞ + save + export |
| LLM analysis | ✕ | match of the day | top-5 | any match |

Enforce limits server-side (single source of truth); frontend only mirrors flags for UX.

**Promo codes**
- Admin generates **batches sized in multiples of 500** (validate `size % 500 == 0`); UI asks
  "how many codes" as a multiple of 500.
- Types: `percent`, `fixed`, `trial` (N days of tier X), `upgrade` (grant tier).
- Binding: optional to a specific user (email/ID), to a tier, `max_activations` (1 or N),
  `expires_at`, batch-wide kill switch.
- Discount stacking rule (configurable): default "not stackable, take max".
- Store `code_hash` (never plaintext); constant-time compare; rate-limit redemption; CSV export.

**Billing seam:** model `subscriptions.source = payment` and a `PaymentProvider` interface now (no
implementation) so YooKassa/Stripe/crypto plug in later without schema change.

---

## 8. LLM analysis (top / match-of-the-day)

- Build a structured feature+probability context, ask the model to **explain** (not invent) the
  matchup narrative. Never present LLM text as a source of the probabilities.
- Configurable in admin: `base_url`, `model`, API key (encrypted), max tokens, daily token budget,
  cache TTL. Cache per fixture; hard-stop on budget. Log tokens & cost to `llm_analyses`.

---

## 9. Admin panel

Dashboard: provider quotas remaining, ingestion job log with progress bars, training status
(MLflow runs: running/done/failed), feature drift, LLM token spend, active users, promo conversion.
Actions: retrain method X, rollback to version N, rescan league, enable/disable provider,
edit tiers/prices/limits, generate/kill promo batches, bind promo to user.
Admin requires 2FA (TOTP). All admin actions written to `audit_log`.

---

## 10. Security (OWASP ASVS target)

- Argon2id password hashing; JWT access (short) + rotating refresh tokens; RBAC (guest/user/admin);
  admin 2FA (TOTP).
- Strict input validation (Pydantic), parameterized queries only, no string-built SQL.
- Security headers: CSP (nonce-based), HSTS, X-Content-Type-Options, Referrer-Policy, frame-ancestors.
- Rate limiting (Redis) on auth, promo redemption, API; account lockout/backoff.
- Secrets only from env/secret store; **encrypt provider & LLM keys at rest**; never log secrets.
- CORS locked to known origins; CSRF protection for cookie-based flows.
- Dependency pinning + automated vuln scanning; no deprecated libs.
- PII minimization; audit log; structured logging with secret redaction.

Deliver **`SECURITY.md`** describing how to pentest the app and with what:
Semgrep + Bandit (SAST), `pip-audit`/`npm audit` + Trivy (deps & images), gitleaks (secrets),
OWASP ZAP baseline (DAST), nuclei, and sqlmap notes for the search/backtester endpoints — with
exact commands and expected clean output.

---

## 11. Testing (mandatory, gate merges)

- **Backend:** pytest + pytest-asyncio, coverage ≥ 80%. Unit tests for every ML method against known
  fixtures (e.g., Dixon-Coles score-matrix sums to 1; Elo update direction; calibration monotonicity;
  de-vig sums to 1). Integration tests for provider abstraction (mocked HTTP), auth, promo logic
  (500-multiple validation, binding, kill switch), tier enforcement.
- **Frontend:** Vitest + React Testing Library; Playwright e2e (guest limits, login, match card,
  backtester run, admin promo generation).
- **Contract tests** for provider DTOs.
- Load-shed/quota tests: ingestion must hard-stop at provider limit.
- CI must be **green before merge** — no exceptions.

---

## 12. GitHub automation

`.github/workflows/`:
- `ci.yml`: lint (ruff, eslint), type-check (mypy, tsc), pytest+coverage, vitest, playwright, build.
- `security.yml`: semgrep, bandit, pip-audit/npm audit, trivy, gitleaks.
- `release.yml`: on tag/main → build & push Docker images to **GHCR**.
- Branch protection: require ci.yml + security.yml green; block merge otherwise.
- `make deploy`: pull GHCR images on the VPS, run Alembic migrations, `docker compose up -d`,
  health-check. Provide `docker-compose.yml` + `docker-compose.prod.yml` + `.env.example`.
- Dependabot enabled.

---

## 13. Repository layout

```
/frontend        Next.js 15 app (RU/EN i18n, design system package)
/backend         FastAPI app
  /app/api       routers
  /app/core      config, security, deps
  /app/models    SQLAlchemy
  /app/schemas   Pydantic
  /app/providers BaseProvider + implementations + id-mapping
  /app/ml        elo, glicko, dixon_coles, xg, lightgbm, market, consensus, calibration
  /app/services  ingestion, backtester, promo, tiers, llm, push
  /app/workers   arq tasks + scheduler
/ml_artifacts    MLflow store
/infra           docker-compose*, Caddyfile, Makefile
/.github         workflows
SECURITY.md  README.md  CONTRIBUTING.md  .env.example
```

---

## 14. Build order (each phase ends only when its tests are green)

1. Repo scaffold, Docker Compose, CI skeleton, health checks, `.env.example`.
2. Auth (email+password, JWT+refresh, RBAC, admin 2FA) + audit log.
3. Data model + migrations + provider abstraction + ID mapping + **football-data.co.uk** historical
   ingestion.
4. ML methods 1–6 + consensus + calibration + MLflow + public performance page.
5. API-Football live ingestion + in-play recompute + WebSocket/SSE streaming.
6. Frontend: design system, match list/card (all method bars + consensus), light sporty theme,
   skeletons, i18n.
7. Tiers + feature flags + server-side limit enforcement + guest blur/lock.
8. Promo codes (500-multiple batches, binding, kill switch, CSV) + billing seam.
9. Strategy backtester (filters, matched count, ROI, equity, drawdown, CI, export).
10. LLM analysis (configurable OpenAI-compatible endpoint, budget, cache).
11. Push (Telegram + Web Push) on probability swings.
12. Admin dashboard (quotas, jobs, training, drift, spend, retrain/rollback).
13. Security hardening pass + `SECURITY.md` + full scan suite green.
14. Release workflow → GHCR → `make deploy`.

---

## 15. Definition of done

- All phases complete; CI + security workflows green on `main`.
- Coverage ≥ 80% backend; e2e passing.
- Light, energetic, responsive UI with loading indicators; RU/EN.
- No plaintext secrets anywhere; provider/LLM keys encrypted; keys never returned to client.
- One-command deploy documented and working; images in GHCR.
- `SECURITY.md` lets me reproduce a pentest with the named tools.

---

## 16. Model governance (admin-controlled) — REQUIRED

The admin must be able to **manage models and their display share/weight** from the UI. Each
prediction method has a governance record:

`model_registry` table:
- `method` (elo | glicko2 | dixon_coles | xg | lightgbm | market | consensus)
- `version`, `mlflow_run_id`
- `is_enabled` (bool) — is the method computed at all
- `is_visible` (bool) — is it shown on the public match card
- `display_weight` (0–100 %) — the method's share in the **consensus** blend
- `accuracy_pct` (rolling, auto-computed) — see below
- `status` (champion | challenger | retired)
- `min_samples`, `last_trained_at`, `notes`

**Accuracy % ("% правдивости"):** computed on a rolling out-of-sample window (default 90 days,
admin-configurable) as a normalized skill score, so all methods are comparable:

```
accuracy_pct = 100 * (1 - brier_score / brier_baseline)
```
where `brier_baseline` is the naive prior (league base rates). Also store raw Brier, log-loss,
ROI vs closing line, and hit-rate. Never compute accuracy on data the model trained on.

**Champion selection (mandatory):**
- A nightly job re-evaluates all methods on the rolling window.
- The method with the **highest `accuracy_pct`** (subject to `min_samples`) is auto-promoted to
  `champion` and gets the largest consensus weight.
- Admin can either keep **auto mode** (weights ∝ accuracy_pct, softmax-normalized) or switch to
  **manual mode** and set `display_weight` per method by hand (must sum to 100 %).
- Any promotion/demotion/weight change is written to `audit_log` and is **one-click reversible**
  (rollback to a previous `model_registry` snapshot).

**Admin UI — "Models" page:**
- Table: method | version | status | accuracy % | Brier | log-loss | ROI | samples | enabled | visible | weight slider
- Toggle auto/manual weighting; sliders locked to sum = 100 % in manual mode.
- Buttons: retrain, promote/demote, rollback version, hide from public.
- The public match card renders only `is_visible` methods and labels the champion with its accuracy %.

---

## 17. Backups & disaster recovery — REQUIRED

Three separate backup tracks. All backups are **encrypted (age/GPG) before leaving the host** and
verified by an automated restore test.

**1. Database (PostgreSQL)**
- Continuous archiving: WAL-G (or pgBackRest) → S3-compatible object storage (any provider).
- Full base backup nightly + WAL shipping → point-in-time recovery (PITR).
- Retention: 7 daily, 4 weekly, 6 monthly (configurable).
- **Weekly automated restore drill** into a throwaway container; CI job fails loudly if restore
  or `pg_dump --schema-only` diff fails. A backup that has never been restored is not a backup.

**2. ML models & artifacts**
- MLflow artifact store on the same S3-compatible bucket (separate prefix), never only on local disk.
- Every trained model version is immutable and retained: model binary + feature schema + training
  data hash + metrics + `model_registry` snapshot. This is what makes **rollback to any previous
  version** possible.
- Retention: keep all champions forever; keep last N (default 10) versions per method.
- Model rollback = point `model_registry` at an older version; no retraining, no downtime.

**3. Config & secrets**
- Repo is the source of truth for config (GitOps). Secrets live in the secret store, backed up
  separately and encrypted; `.env` is never committed.
- Store a signed manifest (hashes) of each backup; alert on any integrity mismatch.

**Monitoring:** alert (Telegram) on backup failure, on restore-drill failure, and if the newest
successful backup is older than the RPO.

**Targets:** RPO ≤ 15 min (WAL shipping), RTO ≤ 1 h (documented runbook in `README.md`).

---

## 18. Horizontal scaling (design now, deploy later)

The app must run on a single VPS today but scale to **multiple servers** without a rewrite:

- **Stateless API:** no in-process session/state — all state in Postgres/Redis. Any number of
  `api` replicas behind Caddy/nginx (or a load balancer).
- **Workers:** ARQ workers are horizontally scalable; separate queues (`ingest`, `train`, `live`,
  `push`, `llm`) so a heavy training job never starves live recomputation. ML worker can move to a
  dedicated CPU-heavy machine by simply pointing it at the same Redis + Postgres + S3.
- **Scheduler:** exactly one `beat` instance; use a Redis lock so a second instance is a hot standby,
  never a duplicate.
- **Idempotency:** every ingestion and prediction task is idempotent and keyed (`fixture_id + method +
  model_version`), so retries and duplicate delivery are safe.
- **Live fan-out:** Redis pub/sub between API replicas so a WebSocket client on any replica receives
  live probability updates.
- **DB:** primary + read replica ready (SQLAlchemy read/write routing behind a single session factory).
- **Object storage:** S3-compatible from day one (MinIO locally, any provider in prod) — no local-disk
  assumptions.
- **12-factor config:** everything from env; no hardcoded hosts. Document the multi-node compose /
  swarm path in `README.md`.

---

## 19. Legal disclaimer & responsible use — REQUIRED

- Persistent, visible disclaimer in the footer, on every prediction card, and in the backtester
  results (RU + EN):
  > *«Информация на сайте носит исключительно аналитический и информационный характер. Прогнозы
  > основаны на статистических моделях, не гарантируют результат и не являются призывом к участию
  > в азартных играх или финансовым советом. 18+.»*
  > *"Analytical and informational purposes only. Predictions are statistical estimates, guarantee
  > nothing, and are not gambling advice or a financial recommendation. 18+."*
- **18+ age gate** on first visit (stored in a consent cookie; re-prompt after expiry).
- Backtester must display the small-sample warning and the phrase "past performance does not predict
  future results" next to any ROI figure.
- Dedicated pages: Terms of Use, Privacy Policy, Responsible Gaming (with links to help resources),
  Disclaimer. All linked in the footer, i18n-ready.
- Cookie/consent banner (analytics only after consent).

---

## 20. Required repository documentation — DATA SOURCES

Create **`docs/DATA_SOURCES.md`** as a first-class deliverable (a starter version is provided in this
repo — extend and keep it accurate as providers are added). It must answer, for every data source:

1. **What it is used for** (role: `historical` / `live` / `odds` / `xg`) and which ML features depend on it.
2. **Where to register**, what the free tier gives, what the paid tiers cost, and which plan we assume.
3. **How to get the key**, step by step, and **exactly where to paste it** (Admin → Providers → Add
   provider → role + limits). Keys are entered in the UI, encrypted at rest, and never returned to
   the client; `.env` keys are only a local-dev/CI fallback.
4. **Endpoints we call**, the polling cadence, and the resulting request budget per day.
5. **Rate limits & quota behaviour** — what the app does when a quota is exhausted (hard stop, no
   overspend, fall back to the next provider by priority).
6. **Coverage caveats** (which leagues have lineups / shot coordinates / xG, and which do not).
7. **Bootstrap procedure**: which command loads the historical dataset, how long it takes, how much
   disk it needs, and how to verify the load (row counts per league/season).
8. **Adding a new provider**: the exact steps to implement `BaseProvider`, register capabilities,
   add ID-mapping rules, and write the contract test. Include a minimal code skeleton.
9. **Legal/ToS notes** per source (redistribution limits, attribution, scraping rules).

Keep the document in sync with `app/providers/` — CI has a check that every implemented provider has
a section in `docs/DATA_SOURCES.md`.
