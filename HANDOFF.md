# BetPulse — Engineering Handoff

Living context document for anyone (human or agent) picking up this project. It captures the
_process rules_, the _current state_, and the _sandbox realities_ that are not obvious from the
code alone. Keep it current: update the "Phase status" table and the "Sandbox / environment
constraints" section whenever they change.

---

## 0. Current state

_Rewritten in every PR. Read this first; read other sections only when the task needs them._

- **main:** `61497fa` (#127, ER2-05 and the Redis launch blockers). **This PR:**
  `docs/deploy-vps`: `docs/DEPLOY_VPS.md`, the operator's runbook **in Russian** (owner's
  exception, recorded in `AGENTS.md`): sizing from the stand, provider criteria, server setup,
  secrets, path A (closed trial, Caddy internal CA) step by step with expected output, the
  first-VPS checks, operations, path B preconditions (technical and "confirm with a lawyer"
  legal items), and a one-page day checklist. With it: `CADDY_TRIAL_TLS=local_certs` (the trial
  certificate switch), `scripts/check-egress.sh`, `scripts/redis-backup.sh`,
  `scripts/redis-restore.sh`, `scripts/backup-now.sh`, `python -m app.cli vapid-keys`, and
  `backend/tests/test_deploy_doc.py` (the runbook names only scripts, `.env` keys, CLI commands
  and sections that exist, and covers every "First VPS launch" item below).
- **Fixed here, from #127 (owner's choice: in this PR):** a restarted Redis container (docker
  restart, a host reboot) crash-looped on Ubuntu: its start script rewrote `/tmp/redis-auth.conf`
  in place, and `fs.protected_regular` refuses even root an `O_CREAT` open of another user's
  file in sticky `/tmp`. Docker Desktop has it off (0), so every local run passed; CI (Ubuntu)
  caught it through this PR's restore test. The script now removes the file first, and
  `redis-config-smoke.sh` restarts the container (red in CI before the fix).
- **Rehearsal stack (2026-10-10):** Redis backup → damage → `redis-restore.sh` and
  `backup-now.sh` ran there (log `rehearsal-logs/redis-restore-20261010T075949Z.log` in the
  clone, Postgres dumps taken first); 9/9 healthy after. The clone is on the PR branch.
- **Open, waiting for the owner (found 2026-10-10, not fixed):** the BFF does not forward the
  browser's `User-Agent` to the API (`frontend/lib/server/backendProxy.ts` relays only
  authorization, content-type, cookie and x-csrf-token), so every browser reaches `/auth/refresh`
  as `user-agent: node`. The F13 B replay's user-agent binding (§6 "Refresh replay") therefore
  always matches; the subnet binding still works. Options in the PR report.
- **F5: fixed in code; verified on a server: NOT YET** — `docs/DEPLOY_VPS.md` §7.3 decides it;
  until then the closed-trial rule holds (80/443 open to the owner's addresses only).
- **Last release:** `v0.0.1-rc6` (pre-release, main `17d508a`, 2026-10-08). Nothing was
  released since; rc7 carries everything since rc6 including this PR (path A needs ≥ rc7).
- **Next:** the v0.0.1-rc7 rehearsal (§9i, "v0.0.1-rc7 (planned)"), then the first VPS by
  `docs/DEPLOY_VPS.md` path A, then the cleanup PR (ER2-09, ER2-13, F15, F16), O2 (with F17,
  F18), live on Sportmonks (§9m queue).
- **Launch blockers still open (§9i):** F5 server verification (runbook §7.3); for path B also
  the runbook's section 9 (legal, off-server backups, registration, real data).
- **Sportmonks trial** (ends ≈ 2026-10-17): the live-format capture is retried when the owner
  says a match is live; fixtures stay uncommitted while a PR is open (§9l).
- **Read for the current work:**
  - Deploying: `docs/DEPLOY_VPS.md`; background §9i (outline, first-VPS checklist), §6.
  - Releases and rehearsals: §9i "Release images and digests", "rc6 rehearsal", "v0.0.1-rc7".
  - Any PR: §2 (rules), §5 (CI, e2e conventions, package-lock rule), §7 (environment).

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
7. **No AI or model attribution** (model names, "generated with", co-author lines) in commits,
   PRs, code, or any pushed artifact.
8. **Verify a merge yourself before any post-merge step** (owner, 2026-10-08). Before deleting a
   branch, syncing `main` or starting the next task: `gh pr view <n>` must show `MERGED`, and
   `origin/main` must contain the merge commit (`git merge-base --is-ancestor <merge> origin/main`).
   A message saying "merged" is not evidence. If the PR is not merged, stop and say so; never
   delete an unmerged branch, and treat git's "not yet merged" warning as a stop signal.
   (Deleting a PR's head branch on GitHub closes the PR: that is how #120 got closed unmerged
   once and had to be restored at the same head.)

## 3. Stack & repo layout

Monorepo:

| Path | Contents |
|---|---|
| `/backend` | FastAPI · Python 3.12 · async SQLAlchemy 2.0 · Alembic · Pydantic v2 · ARQ workers |
| `/frontend` | Next.js 15 · React 19 · TypeScript (strict) · Vitest |
| `/infra` | docker-compose · Caddy · custom MLflow image · Postgres init |
| `/.github` | `ci.yml`, `security.yml`, dependabot |
| `/docs` | `DATA_SOURCES.md` (provider canon) |

Data plane: PostgreSQL 16 + TimescaleDB (hypertables `odds`, `predictions_live`) · Redis 7 · MLflow
(Postgres backend + artifacts on the `mlflow_artifacts` volume, served over HTTP; MinIO removed
2026-10) · ARQ (async Redis queues `realtime`, `batch`, `ml`,
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
| 4 | 6 ML methods + consensus + calibration + MLflow (artifacts on its own volume; MinIO removed 2026-10) + model registry + `/performance` | ✅ merged |
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

**`frontend/package-lock.json`: minimal hunks only.** `npm update <pkg>` rewrites unrelated lock
entries: with npm 11 on Windows it dropped `next-intl/node_modules/@swc/helpers` and the `dev` flag
of `fsevents`; with npm 10.9.9 in `node:22` it moved the `@swc/helpers` entry. So a dependency bump
by hand changes only that package's `version`, `resolved` and `integrity` (from
`npm view <pkg>@<version> dist`), and is validated on a copy in a clean `node:22` container, so
nothing is written into the working tree:
`docker run --rm -v "$PWD/frontend:/src:ro" node:22 sh -c "mkdir /w && cp /src/package.json /src/package-lock.json /w/ && cd /w && npm ci && npm ls <pkg> && npm audit --omit=dev --audit-level=high"`.
`npm ci` must succeed and every copy of the package must be the new version. Example: `0578a72`
(`source-map-js` 1.2.1 → 1.2.2, CVE-2026-93749, 3 changed lines).

Backend CI specifics (`.github/workflows/ci.yml`): Postgres (timescaledb image) + Redis service
containers; installs `libgomp1` for LightGBM; runs the **migration round-trip**
`upgrade head → downgrade base → upgrade head`; runs **offline historical ingestion** against the
committed CSV fixture; then pytest with `--cov-fail-under=80`. MLflow uses a temp **file store** in
CI (`MLFLOW_TRACKING_URI=file://…`, `MLFLOW_ALLOW_FILE_STORE=true`) — the MLflow server (Postgres
+ artifacts volume) only in dev/prod. `make train` is deliberately **not** in CI (LightGBM on real data takes minutes; the
fixture pipeline test covers the full path instead — see the comment in `ci.yml`).

**Playwright security e2e shipped in Phase 13d.** `Browser security (Playwright)` builds the
production frontend and verifies strict CSP nonces, response headers, a violation-free browser boot,
and locale persistence. Failure artifacts are uploaded for debugging. Component behavior remains
covered by Vitest + React Testing Library.

**e2e conventions (2026-10-09).**
- Most specs answer the BFF routes in the browser with `page.route` and need no backend.
  `e2e/refresh-reload.spec.ts` (F13) needs the real BFF: it starts the **test-only** fake
  backend `e2e/support/fakeBackend.ts` on `127.0.0.1:47613` (fails fast with a clear message
  if the port is taken), and `playwright.config.ts` points the e2e web server's
  `API_BASE_URL` there. `lib/testOnlyImports.test.ts` fails if anything outside `e2e/` and the
  Playwright config imports from `e2e/`. The fake has one port, so that spec cannot run in
  parallel with itself (exclude it from `--repeat-each` stress runs with
  `--grep-invert "reload storm"`).
- `<html data-hydrated>` is set by `Providers` after mount (never in the server HTML, no inline
  script). Wait for it before clicking server-rendered markup: a click before React attached its
  handlers is lost (the age-gate spec failed about 1 in 200 runs on 6 workers that way; after
  the fix 200/200 for the gate specs and 240/240 for the whole suite ×20 on 6 workers).

## 6. Conventions that bite if ignored

- **Migrations** are hand-written in `backend/migrations/versions/` (`0001`–`0019` today). Enums via
  `postgresql.ENUM(..., name=...)` created with `checkfirst=True` and `create_type=False` on the
  column; dropped in `downgrade`. Timescale hypertables are guarded by a `pg_available_extensions`
  check so CI (plain PG) and prod (timescaledb) both pass. **Every migration must survive the
  round-trip.**
  - **Server messages are logged** (2026-10-07): asyncpg drops Postgres NOTICE/WARNING unless a
    listener is registered, so `migrations/env.py` forwards them to the `alembic` logger
    (stderr, `INFO  [alembic] postgres NOTICE: …`). A migration that must tell the operator
    something (counts, skipped rows) can `RAISE NOTICE`. stdout (`alembic heads`/`current`, read
    by CI) is unchanged. An upgrade now also shows TimescaleDB's own warnings about `varchar`
    columns in the hypertables; they are harmless.
  - **Offline mode (`alembic upgrade head --sql`) does not work and never did past `0002`**:
    `0003`'s Timescale check queries the database (`AttributeError: 'NoneType' object has no
    attribute 'scalar'`). Not used anywhere; fix only if SQL scripts are ever needed.
- **Secrets** (TOTP, provider/LLM keys) are Fernet-encrypted at rest with `DATA_ENCRYPTION_KEY`;
  never returned to the client (masked suffix only). Provider keys are entered in the Admin UI;
  `.env` values are a dev/CI fallback only.
- **Every route has an access policy (`tests/api/test_authorization_matrix.py`).** `ROUTE_POLICY`
  holds one row per (method, path): `public`, `public_tier`, `user`, `push_tier`, `stream_tier`,
  `csrf` or `admin`. A new route fails CI until it gets a row; each row must match the route's
  dependency tree (`require_admin`, `get_current_user`, `require_push_tier`,
  `require_streaming_tier`, `verify_csrf`, `get_tier_context`); every `/admin/*` route is admin;
  public routes are listed in `PUBLIC_ROUTES` with a reason each and capped at 11 — adding one is
  a security decision. The matrix calls every route as anonymous, garbage/expired token, inactive
  user, inactive admin, free user, expert user, admin without 2FA, admin who must change the
  password, and admin, and expects 401/403/"allowed" (anything but 401/403) per policy. A tier
  gate inside a handler is declared in `HANDLER_TIER_GATES`; a route whose allowed call cannot be
  made (the SSE stream) in `ALLOWED_CALL_SKIPPED`, both with a reason. BOLA tests cover
  strategies (404), push subscriptions and follows (scoped, idempotent deletes: 204/200 and the
  other user's row survives).
- **Request bodies forbid unknown fields; a 422 never echoes the request.** Every model FastAPI
  parses as a body, and every model nested in one, derives from `app.schemas.base.RequestModel`
  (`extra="forbid"`): a misspelt or smuggled field is a 422 `extra_forbidden`, never dropped.
  Response models and provider DTOs stay lenient. `app/core/validation.py` replaces FastAPI's
  default 422: each error keeps only `type`, `loc` (parts capped at 64 chars) and `msg` — no
  `input` (it carried short passwords and over-long API keys back to the client) and no `ctx` —
  and logs only method, path and `(type, loc)`. Our own validators' `ValueError` messages name
  fields, never submitted values. `tests/api/test_request_validation.py` walks the app's routes
  and fails on any body model without `forbid` (its allowlist is empty and stays empty) and on
  any `*Request/*In/*Create/*Update/*Assign` schema not based on `RequestModel`. The frontend
  reads only `detail.tier_required` from error bodies.
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
- **Refresh replay (F13 B, 2026-10-09).** A rotation T1 → T2 whose answer was lost must not
  end in a logout. After the commit, `rotate_refresh_token` stores an entry in Redis
  (`auth:refresh_replay:<sha256 of T1>`, a hash: `data` = Fernet(`DATA_ENCRYPTION_KEY`) of T2,
  T2's id, an HMAC (`SECRET_KEY`) of the user-agent, the client subnet (IPv4 /24, IPv6 /64,
  `core.client_ip.replay_subnet`) and the account's `credentials_changed_at`; `uses` = 0), TTL
  `REFRESH_REPLAY_WINDOW_SECONDS` (60; 0 turns it off; otherwise between the grace window and
  300). No entry without a user-agent or a known client address. When T1 comes back, under the
  family lock and **before** the grace check, `_try_replay` returns **the same T2** and a new
  access token — no refresh token issued, audit `auth.token.refresh_replayed` (`family_id`,
  `uses`, `age_seconds`) — only if all hold: the entry decrypts and names T1's direct
  replacement; T2 is not rotated (`t2_used`) and not revoked or expired (`t2_revoked`); T2 is
  ≤ the window old on the DB clock (`window_passed`); the account is active (`user_inactive`)
  and its `credentials_changed_at` is unchanged (`credentials_changed`); the user-agent HMAC
  and the subnet match (`ua_mismatch`, `subnet_mismatch`); fewer than
  `REFRESH_REPLAY_MAX_USES` (3) replays of this T1 so far (`cap_reached`, Lua, atomic); and the
  client IP bucket has fewer than `RATE_LIMIT_REFRESH_REPLAY_PER_MINUTE` (10) replays this
  minute (`ip_limited`; the cap is given back, so only successful replays count). Otherwise the
  rules above apply unchanged (409 inside 10 s, family revocation after) and the reason goes
  into that audit row's `meta.replay` (also `no_entry`, `entry_invalid`, `store_unavailable`).
  Redis is best effort: every call is bounded to 0.5 s, a failure or timeout is logged and means
  "no replay", never a failed refresh; the ordinary path makes no Redis call under the lock.
  **Not a theft control until F5 lands** (§9m, F13). No migration; rolling the image back
  leaves only entries that expire within 60 s. Tests: `tests/test_refresh_replay.py`, every
  window boundary on both sides by moving `created_at` on the DB clock (no sleeps).
- **Public request limits (F7, 2026-10-09).** `SECURITY.md` has the table of every limit
  (identity, threshold, position, behaviour without Redis). `GET /matches` (120 / 60 s,
  `RATE_LIMIT_MATCH_LIST_*`), `/matches/{id}` (120 / 60 s) and `/analysis` (20 / min) enforce
  their limit in **one route dependency**, `deps.public_rate_limit(scope)`, in the decorator's
  `dependencies` so FastAPI runs it **before `get_tier_context`**: over the limit neither the user
  nor the tier is loaded (a test forbids both). The identity, `deps.rate_limit_identity`, is the
  `sub` of an access token with a valid signature, type and expiry, as a canonical UUID, read
  **without a database query**; a token that fails verification or has no UUID subject is a guest
  (IP bucket, IPv6 /64). The key holds only that UUID or the bucket, never token material. These
  three fail closed on a Redis error, like before. `POST /auth/refresh`: per client IP, 120 / min
  (`RATE_LIMIT_REFRESH_PER_MINUTE`), every attempt counted, **before the rotation** (a 429 rotates
  nothing and sets no cookie); **fails open** (no limit, a warning, the call bounded to 0.5 s)
  because the rotation needs only the database and failing closed would stop every session
  renewal during a Redis outage. A request with a replay spends one unit of this limit, and a
  successful replay also one of its own 10 / min.
  - **Client** (`lib/auth/store.ts`): a refresh answered 429 keeps the session (no sign-out; the
    "retrying" notice), waits `Retry-After` clamped to 1–300 s (60 s when missing) plus 0–5 s of
    jitter, and sends no refresh before then from any trigger (timer, focus, online, visibility,
    an API call: it uses the still-valid token or fails without sending, F9). Data views follow
    F8's rules everywhere: an error replaces content only on a first load; a failed refetch keeps
    it with the stale note (`components/match/StaleNote.tsx`); after a 429 the list and the match
    card poll after max(60 s, `Retry-After`).
  - **Known risk, accepted (owner, 2026-10-09; no code):** a lost rotation answer followed by a
    429. All of these must hold: (1) T1 → T2 was rotated and the answer never reached the browser
    (F13 B's case); (2) the next refresh with T1 from that client meets the per-IP limit — its
    address has made more than 120 refreshes that minute, i.e. many users behind one NAT or an
    abusive neighbour; (3) the 429's wait (`Retry-After` ≤ 60 s from the fixed window, plus up to
    5 s of jitter) ends after T2's 60 s replay window, measured from the rotation, has passed;
    (4) T2 is still unused (no other tab of the browser renewed with it). Then T1 comes back more
    than 10 s after the rotation, outside the replay window: reuse, the family is revoked and the
    user is signed out ("session expired"). With 120 / min per address an ordinary user does not
    reach (2). If it shows in practice: answer a 429 only after the replay check (a T1 whose entry
    qualifies is served), or cap the client's wait at the remaining replay window.
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
- **Account security limits (F10 / ER2-02, 2026-10-08).** Per user, fixed windows
  (`rate_limit.enforce_user_limit`, keys `rl:{scope}:user:{id}`), as **route dependencies** so
  they run before the handler body: `/auth/change-password` 5 per 15 min before Argon2;
  `/auth/2fa/enable` and `/auth/2fa/disable` 5 per 15 min (one shared bucket) before the code
  check; `/auth/2fa/setup` 5 per hour before a new secret. Every attempt counts, successes
  included; over the limit → 429 `Too many attempts` with `Retry-After`. Settings:
  `RATE_LIMIT_PASSWORD_CHANGE_ATTEMPTS`, `RATE_LIMIT_TOTP_CODE_ATTEMPTS`,
  `RATE_LIMIT_ACCOUNT_SECURITY_WINDOW_SECONDS`, `RATE_LIMIT_TOTP_SETUP_PER_HOUR`. Login keeps
  its own rules: per-IP 5/min first, then the per-account failure counter and lockout, which a
  wrong TOTP code feeds exactly like a wrong password.
- **Access tokens die when the credentials change (ER2-01, 2026-10-09).**
  `users.credentials_changed_at` (migration 0019, NULL until the first event) is set by a
  password change, `reset-2fa` and an admin disabling the account
  (`services.auth.mark_credentials_changed`; each audit row carries the value). Every access
  token carries it as the `cca` claim — whole microseconds since the epoch, integer arithmetic,
  or null — and `core.security.token_state` accepts a token only when the claim **equals** the
  column: no comparison with `iat` (whole seconds), so a token from the second of a change is
  decided correctly both ways. A token **without** the claim (issued before 0019) is accepted
  while the column is NULL (the deploy signs nobody out) and, once it is set, only when `iat`
  is a whole second later. A non-null claim against a NULL column (0019 downgraded and upgraded
  again) is refused; a refresh repairs it. A claim of any other type (string, float, bool) is an
  invalid token: 401 on protected routes, guest on public ones, never a 500.
  - A genuine, unexpired but **revoked** token gets **401 `Session revoked` with
    `X-Session-Revoked: true` on every route, public ones included** (`get_current_user` and
    `get_optional_user`, raised from the auth dependency before the route runs). Expired or
    garbage tokens on public routes stay a guest, as before.
  - The BFF relays it on every path: `RELAYED_RESPONSE_HEADERS` in `lib/server/backendProxy.ts`
    (`retry-after`, `x-2fa-required`, `x-session-revoked`) is shared by `proxyBackendGet` and
    `proxyAuth`; `routeHandlers.test.ts` keeps handlers from calling `backendFetch` or building
    their own responses (exceptions with reasons: `health`, `ready`).
  - The client sends a bearer only through `authFetch` (`lib/auth/store.ts`; `bearer.test.ts`
    forbids `authHeaders()` elsewhere). On 401 + `X-Session-Revoked` for a request that carried
    a token: one forced refresh, one replay. Refresh works (another tab of the same browser
    changed the password, the cookie is already new) → the replay goes out with the new token;
    refresh refused (another device) → signed out with "session expired"; the replay revoked
    again → signed out; the refresh unreachable → the session stays and the 401 is returned.
    Replaying a mutation is safe because the 401 comes from the auth dependency, before the
    route did any work.
- **Database timeouts (ER2-05, 2026-10-10).** API connections carry `statement_timeout` 15 s and
  `lock_timeout` 5 s (`app/core/db.py`, set per connection through asyncpg `server_settings` on
  the request, read and security engines). `create_app()` calls `enable_api_db_timeouts()` first,
  which marks the process as an API process and **refuses once an engine exists**; workers, the
  CLI and Alembic (its own engine in `migrations/env.py`) never import `app.main`, so training,
  ingestion and backfills run unbounded (`tests/test_db_timeouts.py` pins all of it, including
  that the worker, CLI and healthcheck modules never import `app.main`). The test session is an
  API process: `conftest.py` imports `app.main` before any engine. A request query that must run
  longer uses `SET LOCAL statement_timeout` inside its transaction, never a global exemption.
  `idle_in_transaction_session_timeout` waits for ER-H-05 (§9m).
- **Redis: password, memory, persistence (2026-10-10).** One `redis` service (infra compose);
  `scripts/check-compose-ports.sh` checks every point below on the rendered production config and
  `scripts/tests/redis-config-smoke.sh` on a running container (CI).
  - **`requirepass`** from `REDIS_PASSWORD`, required in production (settings refuse an empty,
    placeholder, short or non-alphanumeric one; generate with `openssl rand -hex 32`). The
    container writes it to `/tmp/redis-auth.conf` at start, so it is not in the process
    arguments. It **removes** that file first, never rewrites it: on a restart the file belongs
    to the redis user, and Ubuntu's `fs.protected_regular` makes an in-place rewrite fail even
    for root (fixed 2026-10-10; Docker Desktop does not show it). Clients: the prod overlay renders `REDIS_URL=redis://:<password>@redis:6379/0`
    for the API and every worker — **every image reads that URL**, so a rollback to an image
    from before this change still connects (rc6 images verified on the rehearsal stack). In code
    every client (API client and ARQ pool, worker settings, worker healthcheck) uses
    `Settings.redis_dsn`, which puts `REDIS_PASSWORD` into a URL that has none; a URL password
    that differs from `REDIS_PASSWORD` is refused. Development runs without a password.
  - **`redis-cli` exits 0 on `NOAUTH`.** The healthcheck greps for `PONG`; every `redis-cli` in
    `scripts/` and the Compose files sets `REDISCLI_AUTH` from the container's own
    `REDIS_PASSWORD` (the password never leaves the container) or goes through `redis_cli` in
    `scripts/redis-persistence.sh`; `tests/test_redis_cli_auth.py` fails on any other.
  - **`maxmemory 256mb`, `maxmemory-policy noeviction`.** Every quota, rate-limit, seen-set and ARQ
    key carries a TTL, so any `volatile-*` (and any `allkeys-*`) policy could evict exactly them: a
    fresh quota, a match charged again, a lost job. A full Redis refuses writes instead (quotas
    and the other fail-closed limits answer as if Redis were down; the refresh limit fails open,
    "Public request limits" above). The admin system health component **`redis_memory`** shows
    used memory against `maxmemory` and turns **degraded above 80 %**, or under any evicting
    policy; `not_configured` without a `maxmemory` (the test Redis). 256mb leaves the 512M
    container limit room for the fork of an AOF rewrite or RDB save; resize from `INFO memory` if
    the component ever warns (the rehearsal stack used ≈ 2 MB).
  - **Persistence: AOF, `appendfsync everysec`, plus the RDB snapshot (`save 60 1`).** On a crash
    AOF loses at most ≈ 1 s of writes (a few quota increments: a client gets slightly more; a job
    enqueued in that second is gone); RDB alone lost up to 60 s (every job enqueued and every
    counter increment since the last snapshot). Redis loads the AOF; the RDB is a second copy.
  - **Moving an existing Redis to AOF.** Redis 7.4 started with `--appendonly yes` over an
    RDB-only volume **starts empty** (it creates a new, empty AOF and ignores `dump.rdb`; checked
    2026-10-10, and `scripts/tests/redis-aof-migration-test.sh` keeps a control scenario for it).
    `CONFIG SET appendonly yes` on the running Redis first rewrites the whole dataset into the AOF
    (keys and TTLs kept). `deploy.sh` refuses (exit 1, nothing changed) while Redis holds an
    RDB-only dataset and prints the procedure: `scripts/redis-enable-aof.sh` (BGSAVE awaited, the
    dump copied out of the container into `.release/redis/dump-<UTC>.rdb` with its `.sha256`, then
    AOF on and the rewrite awaited; a stopped Redis is converted in a temporary container), then
    the same deploy command. `rollback.sh` never restarts Redis, so it does not check.
  - **Backup and restore (2026-10-10).** `scripts/redis-backup.sh` (BGSAVE awaited, the dump
    copied out with its sha256; the same `redis_dump_copy` as the AOF move) and
    `scripts/redis-restore.sh COPY --replace-current-data` (sha256 checked, the current data
    copied first, redis stopped, the copy put into the volume without the AOF, converted by
    `redis-enable-aof.sh`'s stopped path, redis started). Proven by scenario D of
    `scripts/tests/redis-aof-migration-test.sh` and on the rehearsal stack.
- **Manual backups until WAL-G (2026-10-10).** `scripts/backup-now.sh` writes
  `.release/backups/<UTC>/` (both `pg_dump -Fc`, the MLflow artifact tar from the mlflow
  container, a Redis copy, `SHA256SUMS`) and checks every part; a failure stops before
  `SHA256SUMS`. A plain `pg_dump > file` that fails leaves an empty file and exit 0 in a
  pipeline — that is why the check exists. Postgres restore from these dumps is **not
  rehearsed** (TimescaleDB needs its pre/post-restore steps); it comes with WAL-G.
- **Closed-trial certificates (2026-10-10).** `CADDY_TRIAL_TLS=local_certs` (`.env`, passed to
  caddy by the prod overlay) puts Caddy's `local_certs` global option in `infra/Caddyfile`:
  the certificate for `PUBLIC_DOMAIN` comes from Caddy's own CA. Empty = public ACME.
  `check-compose-ports.sh` refuses any other value and prints the mode; CI validates both modes
  and `caddy-domain-smoke.sh` checks the issuer. Switching it off stops serving the local
  certificate (checked in a container; storage is per issuer).
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
- **Model governance (registry integrity, live label, traceability).** *One global champion is
  enforced by the database:* partial unique index `uq_model_registry_single_champion` on
  `model_registry (status) WHERE status = 'champion'` (migration `0017`; the upgrade STOPs with the
  list if more than one champion already exists — demote the extras, then re-run). Every path that
  changes the champion (`apply_champion_selection`, admin promote, snapshot rollback) demotes and
  **flushes** the old champion before promoting the new one (`registry.demote_champions`) — never
  rely on an incidental flush. A rollback demotes champions not named by the snapshot (e.g. a version
  trained later); a legacy snapshot with two champions is refused (409
  `snapshot_has_many_champions`). *Registry lock* (`app/ml/registry_lock.py`): a Postgres advisory
  xact lock (two-int key space, apart from the refresh-token family keys). The nightly
  `reevaluate_champions` **tries** it and skips the run if busy (the Redis lock in the ARQ task
  stays); every admin registry write (PATCH, weighting mode, weights, promote, demote, rollback)
  **waits** at most `REGISTRY_LOCK_TIMEOUT_MS` (5000) and then answers **409 `registry_busy`** —
  never a hung request. *Live label:* the in-play numbers come from a **baseline** (Dixon-Coles
  in-play formula with fixed league-neutral rates — same numbers for every pairing at a given score
  and minute). It is stored and served as method `live_baseline`, model version `live-baseline-v1`,
  `team_strength: false`, and the UI shows «Базовая in-play модель: учитывает только счёт и минуту,
  без силы команд» under the notify toggle. Push texts were **reviewed, not disabled**: Telegram and
  Web Push name the basis (the same note) and claim nothing about team strength or an edge.
  Team-aware live base rates are a separate PR. *Traceability:* every served method bar carries the
  predictions' `model_version` and that version's `mlflow_run_id` (from the registry); the consensus
  likewise (`consensus_model_version` / `consensus_mlflow_run_id` on the list and the card).
  *Features:* `rolling_xg_*` for a team with no shot history takes its **league's running mean** of
  approximate xG per team-match over matches **before** the batch (`DEFAULT_LEAGUE_XG` = 1.35 until
  the league has data), not a fixed 1.35.
- **Data-quality report (read-only, safe on prod):** `make data-report REPORT_ARGS="[--leagues
  EPL,LALIGA] [--seasons 2025-2026] [--json] [--strict]"` (`app/services/data_quality.py`). Per
  league/season: fixtures vs expected `n×(n−1)`, teams, % with score, % with a known kickoff time
  (`fixtures.kickoff_time_known`), closing 1X2
  coverage **per bookmaker** and O/U 2.5 coverage. **Errors** (exit 1): the same pairing in a league
  within ±36 h under any season label (cross-source duplicate, found even when the partner sits
  outside `--seasons`), finished without score, impossible score (<0, >15, HT>FT), finished with a
  future kickoff, a price ≤ 1.0 or > 1000, a *closing* quote dated after kickoff. **Warnings** (exit 1
  only with `--strict`): incomplete odds snapshot, 1X2 overround outside [1.00, 1.25], non-canonical
  season label, team count not 16/18/20, and — only for seasons with no fixture in 60 days —
  missing fixtures / uneven team schedules, plus closing-1X2 coverage below 90 %. Run it before any
  data migration to see conflicts upfront.
- **Fixture identity is provider-agnostic (HI-2).** Historical sources plug in as a `SourceAdapter`
  (`app/services/ingestion/core.py`: `name`, `licensed_for_production`, `may_seed_canonical`,
  `fetch(league, season) -> list[FixtureDTO]`), so the paid provider needs an adapter, not a schema
  change. Identity per record: (1) `fixture_external_refs (provider, external_id)` — football-data
  has no ids, so it uses `fd:{div}:{season}:{YYYYMMDD}:{home}:{away}`; (2) **cross-source dedup** —
  same league and canonical teams with a kickoff within **±36 h** under any season label is the same
  match: the new ref is linked to it (37 h, or a second leg a week later, is a different fixture);
  (3) otherwise insert (`uq_fixture_identity` remains the last guard). Live polling uses the same
  path (API-Football's fixture id as its ref). **Seasons** are canonical per league
  (`app/core/seasons.py`, `leagues.season_start_month`: split-year leagues `YYYY-YYYY`, calendar-year
  `YYYY`; API-Football's `"2026"` → `"2026-2027"`). **Kickoffs** are tz-aware UTC (the DTO rejects
  naive datetimes); a date-only source stores 12:00 UTC with `kickoff_time_known = false` — the API
  sends the flag and the UI shows the date only; same-day date-only fixtures share an instant, i.e.
  one ML chronology batch. Time-zone conversion uses `zoneinfo` + the pinned `tzdata` package, never
  the DB server's tz files. **Licence gate fails closed:** in `ENVIRONMENT=production` an adapter
  runs only with an explicit `licensed_for_production = True`; football-data is `False` (CLI exits
  2, admin re-scan answers 409). The live poller (API-Football) is not a historical adapter and is
  not gated by this flag. **Teams** are unique per `(country, normalized_name)` (NULLS NOT
  DISTINCT); a provider's stable team id (`provider_team_aliases.external_id`) is tried before the
  name; a strict provider's unknown name lands in `provider_unmapped_teams` —
  `python -m app.cli unmapped-teams [--provider P]` lists the worklist and
  `python -m app.cli map-team --provider P --alias NAME (--team-id UUID | --team NAME [--country C])
  [--external-id ID]` binds it (audited as `ingestion.team.mapped`).
- **Odds, corrections and resumable runs (HI-3).** *Odds kinds:* the ingestion core enforces the
  rule on the way in — price in `1 < price <= 1000`, a closing quote has `ts <= kickoff`, a
  pre-closing one `ts < kickoff`, checked against both the source's and the stored fixture's
  kickoff (they differ for a cross-source link); anything else (e.g. an in-play price labelled closing) is rejected and logged
  (`odds_quote_rejected`). `closing_quotes` reads **only** `is_closing` rows, so a fixture with
  just pre-closing quotes has no closing price and is excluded from ROI vs closing / market model /
  backtester. football-data pre-closing quotes (`PSH`, `P>2.5`) sit at `kickoff − 1 day` — an
  approximation documented in `docs/DATA_SOURCES.md`. *Reference bookmaker* per kickoff date:
  `REFERENCE_BOOKMAKERS` (JSON list of `{bookmaker, from_date, until_date}`; validated contiguous,
  no gaps/overlaps, open at both ends), default Pinnacle before 2025-07-23 and `market_avg` from that date (inclusive).
  *Corrections:* when a record finds an existing fixture, empty fields are filled from any source;
  the fixture's **own** source may correct the score — and, found by its own id, move the kickoff
  (postponement; no quote taken for the old date stays closing; when the kickoff moves earlier, quotes at or after the new kickoff are removed, counted in the audit as `odds_removed`) — audited as
  `ingestion.fixture.corrected`; a **different** source disagreeing with a stored score changes
  nothing and is recorded in `ingestion_conflicts`, shown by `data-report` as
  `score_conflict_across_sources`. *Runs:* `run_recorded_ingestion` (admin re-scan, ARQ task and the
  `bootstrap-history` CLI) commits after **each** league/season, stores the payload's
  `content_sha256` and skips a pair whose payload is unchanged since its last successful run
  (`skipped_reason = "unchanged"`; `--force` re-ingests). `running` rows older than
  `INGESTION_STALE_RUN_MINUTES` (60 = 2× the batch job timeout) are marked failed at the next start;
  younger ones may be alive and are left alone. Migration `0016` adds the columns and
  `ingestion_conflicts` (schema only).
- **Migrations 0014/0015 (HI-2)** — 0014 schema (refs, `kickoff_time_known`, `season_start_month`,
  alias `external_id`, unmapped worklist, team key), 0015 data (canonical season labels in
  `fixtures`/`backtest_features`/`ingestion_runs`; football-data kickoffs from "UK wall-clock stored
  as UTC" to real UTC, 00:00 → 12:00 UTC + `kickoff_time_known=false`, their quotes moved to the new
  kickoff; football-data refs back-filled). The whole `alembic upgrade` is **one transaction**
  (`migrations/env.py`); every key change is pre-checked and a conflict **STOPs** with the list —
  nothing is merged and the DB stays at its previous revision. Downgrade restores the old kickoff
  convention (season labels stay canonical) and itself stops if two countries now share a team name.
  Tested end-to-end against throwaway databases (`tests/migrations/`). **Before deploying them run
  the pre-deploy checklist in §9i.**
- **Idempotency everywhere.** Ingestion upserts use `ON CONFLICT DO NOTHING` on identity keys
  (`uq_fixture_identity`, odds identity, prediction identity). Tasks keyed by
  `fixture_id + method + model_version` so retries/duplicate deliveries are safe.

## 7. Development environment (important)

The development machine is **not** the production environment. Since 2026-10 it is **Windows +
Git Bash + Docker Desktop** (WSL2); the earlier Linux sandbox notes no longer apply.

- **Backend tests run in a Linux container**, not on the Windows host (through the WSL port relay a
  host pytest against Postgres in Docker takes over 45 minutes; in a container about 8). Containers
  `bp-test-pg` (the CI Timescale image) and `bp-test-redis` (`redis:7.4-alpine`) on a `bp-test`
  network, and a runner image (`python:3.12-slim` + `libgomp1` + the backend deps with the `dev`
  extra) started with the CI environment from `ci.yml`. Mount the **whole repository**, not only
  `backend/`: some tests read repo-root files.
- **Tools not installed on Windows run in Docker:** shellcheck, trivy, Linux pytest, `node:22` (the
  `package-lock.json` check in §5). Worktrees check out CRLF (`core.autocrlf`); strip `\r` before
  shellcheck.
- **Rehearsals** run `scripts/deploy.sh` on GHCR images in a separate scratch clone, never in the
  working copy (it holds the owner's own `.env`), under compose project `betpulse`. That stack
  restarts with Docker Desktop; leave it alone unless rehearsing. In Git Bash set
  `MSYS2_ARG_CONV_EXCL=/dev/null` for `deploy.sh`.
- **The committed football-data CSV fixture** is a documented, format-faithful reconstruction
  (real EPL 2023-24 matchday-1 scores + representative odds), flagged in the Phase 3 PR — not a
  live download.
- **MLflow 3** refuses a file store unless `MLFLOW_ALLOW_FILE_STORE=true` (set in tests/CI).
- Never disable TLS verification.

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
- **matches/day** counts **distinct matches** opened with `GET /matches/{id}` per caller (user id,
  or the guest's client IP from `app.core.deps.get_client_ip`, IPv6 bucketed per /64) and UTC day
  (F6, fixed 2026-10-07): the page refetches its card every 60 s, and the same match again that
  day costs nothing. Redis keys, both with TTL = seconds to next **UTC midnight** (not rolling
  24h): the counter `limits:{id}:{YYYY-MM-DD}` and the set of fixture ids already charged,
  `limits:seen:{id}:{YYYY-MM-DD}`, updated together by one Lua script
  (`counters.incr_distinct_within_limit`), so tabs opening the same new match at once take one
  unit. Over budget, a match not seen today → `403 {tier_required}` (guest→free, free→pro); it is
  not recorded, and a match already seen today still answers 200. The counter is the measure of use
  (`matches_remaining`); a seen set lost on its own makes the next view count again, never an error.
  No migration: an older image ignores the set and keeps counting every fetch on the same counter,
  so a rollback in either direction only changes how fetches are counted that day. The list is free
  and reports `matches_remaining`.
- **SSE gating** now reads the same `live_recompute` flag (was the `UserTier.can_stream_live` enum).
- **Revoked sessions (ER2-01) differ from expired tokens:** an expired token on a public route
  is still treated as a guest (F9: the client renews before expiry), but a token revoked by a
  credentials change answers 401 + `X-Session-Revoked` everywhere, and `authFetch` renews or
  signs out on that very request (§6).
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
- **Cache + budget + cost.** Analyses are cached per `(fixture_id, model, language)` (unique
  constraint, migration `0018`, ER-H-02); a
  cached row older than `cache_ttl_seconds` is regenerated, never served stale (`cached: true/false`
  on the response). A per-UTC-day Redis counter `llm:budget:{YYYY-MM-DD}` hard-stops generation once
  `daily_token_budget` is spent → `{"status": "budget_exhausted", "resets_at": "<UTC midnight ISO>"}`.
  Every generation (not a cache hit) is appended to `llm_generations` with both token counts and the
  cost computed from `cost_per_1k_*` **at write time**, in the same transaction as the cache upsert;
  the admin spend dashboard (Phase 12) sums the journal, never the cache rows, and a later price
  change does not rewrite past spend. Token/cost are **not** exposed on the public response.
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
- **Web Push = tickle only (no payload crypto).** `send_webpush` posts an empty body. The service
  worker (`frontend/public/sw.js`) shows a generic, localized notification (ru, else en) and opens
  the home page on click, or `/matches/{id}` if a push ever carries a fixture id (UUIDs only, never
  arbitrary text in the URL). On install it calls `skipWaiting()`, and on activate
  `clients.claim()`, so a new version takes over open tabs at once. Bump `SW_VERSION` on every
  change. `frontend/lib/serviceWorker.test.ts` runs the worker in a fake scope.
  - **The snapshot endpoint was removed (2026-10-05, audit A3).**
    - **Why.** `GET /live/push/latest/{id}` and its BFF route were public and served in-play
      probabilities to guest and free users, who may not open the live stream
      (`live_recompute`). Nothing legitimate used it: the push body is empty, so the worker never
      knew a fixture id.
    - **Bring it back only together with:**
      1. RFC 8291 payload encryption carrying the fixture id;
      2. a tier check (`live_recompute`);
      3. authorization that works without the user's access token, which the service worker does
         not have. For example, a short-lived signed token placed in the encrypted payload and
         bound to (user, fixture); never a public GET.
- **Dead endpoints are pruned.** A Web Push `404/410` raises `PushGone`; `dispatch_push` deletes that
  subscription row so it is not retried forever.
- **Web Push SSRF guard (fixed 2026-10-04, audit finding A1).**
  - **The bug.** `POST /live/push/subscribe` stored any string as a Web Push endpoint, and
    worker-realtime POSTed to it on every swing push. A Pro user could make the server call
    `http://mlflow:5000`, `http://api:8000` or a cloud metadata address (blind: empty body,
    response not returned).
  - **The guard** (`app/services/live/push_endpoint.py`) runs when a subscription is stored
    (422 `Unsupported push endpoint`) **and** before every send:
    - URL shape: `https` only, default port, no userinfo, a host name (no IP literal), ASCII,
      at most 512 characters;
    - host allowlist: `WEBPUSH_ALLOWED_HOSTS` (default `fcm.googleapis.com`,
      `updates.push.services.mozilla.com`, `web.push.apple.com`, `.notify.windows.com`). A
      leading `.` means any subdomain, and lookalikes such as `fcm.googleapis.com.evil.example`
      fail;
    - DNS: **every** answer must be public. Refused classes: private, loopback, link-local
      (`169.254.169.254`), CGNAT (`100.64/10`, which includes Alibaba's metadata address),
      multicast, reserved, documentation and benchmark ranges; IPv6 ULA (`fd00:ec2::254`),
      site-local, link-local and scoped addresses; IPv4 inside IPv6 (mapped, NAT64, 6to4, Teredo)
      is judged by its IPv4 address.
  - **Sending.**
    - The request goes to the **checked IP** (pinned), with `Host` and TLS SNI set to the push
      host, so the certificate is still verified for the name and a DNS rebinding between check
      and connect is never followed.
    - Redirects are not followed; a 3xx counts as a failure.
    - Timeout is 10 s. The response body is never read or logged.
    - Nothing is ever sent to a refused endpoint, but the two kinds of refusal are handled
      differently:
      - a wrong shape or a host off the allowlist (e.g. a row stored before the guard) can never
        work, so it is pruned like a 404/410;
      - a DNS answer that is non-public, unparsable or empty might be the server resolver's
        fault (a sinkhole, split horizon), and pruning on it could wipe every subscription. It
        is a plain `PushError`: retried once, and the row is kept.
    - A DNS failure, a timeout, or a TLS or connection error is also a plain `PushError`. It is
      logged with the exception class only, because httpx messages carry the URL.
    - `push_task` now commits its session. Before, every prune (404/410 included) was rolled back
      when the task ended, so dead rows were retried on every push.
  - VAPID signing is unchanged; the JWT `aud` is still the push service's origin.
  - Checked live from a workstation: FCM answered 410 and Apple 400 through the pinned address.
    Mozilla was unreachable from that network even with plain curl.
  - **Defence in depth, not done:** egress segmentation for `worker-realtime`, so it can reach
    only DNS, the push services, Telegram and the live data provider, and not the internal
    `mlflow`/`api` services or the metadata address. Options: a separate Compose network without
    the internal services plus a host firewall (`DOCKER-USER` chain) for metadata, or an egress
    proxy with a host allowlist. Revisit after the audit PRs.
- **Telegram deep-link.** `telegram_link_tokens` (SHA-256 hash only, single-use `used_at`, 15-min
  expiry — the DB row is the sole source of truth, no Redis copy). `POST /push/telegram/link` mints
  `t.me/<bot>?start=<token>` (Pro/Expert); Telegram's `/start` hits `POST /push/telegram/webhook`,
  authenticated by `X-Telegram-Bot-Api-Secret-Token` compared with `hmac.compare_digest`. A
  missing/wrong secret is logged and answered **200 OK (empty)** so Telegram never retries; only a
  valid `/start <token>` records the chat id as a Telegram `PushSubscription`. `DELETE /push/telegram`
  disconnects.
  - **That is the only way in (fixed 2026-10-04, audit finding A2).** `POST /live/push/subscribe`
    used to accept `channel=telegram` with any chat id, which skipped the `/start` proof and let a
    user point pushes at someone else's chat. It now answers 422 for Telegram and takes Web Push
    only. Production never ran, so no unproven Telegram rows exist.
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

1. **Extended system health + real readiness** (readiness part: **ER-M-01** in §9m) — add
   components for API readiness, ARQ worker/queue depth, latest ingestion run, latest model
   re-evaluation, today's LLM token spend, and backup status (`not_configured` until Phase 14
   backup lands). Upgrade `/health/ready` from the process-only stub
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
  uvicorn runs with `--no-proxy-headers`. Compose pins a dual-stack network and the `web`/`caddy`
  addresses in both families (`BETPULSE_NETWORK_SUBNET(6)`, `BETPULSE_WEB_IP(6)`,
  `BETPULSE_CADDY_IP(6)`) and trusts exactly those /32s and /128s; production refuses to start
  without an explicit, private, narrow list, and without `INTERNAL_NETWORK_CIDRS` (the network's
  subnets, holding every trusted proxy). A client identity inside them is logged once per process
  and shown on the admin health page (`client_ip`), F5 (§9m). Rate-limit and guest-quota
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
- **Published ports (fixed 2026-10-03).** Only Caddy may publish ports (80/443).
  - **The bug.** The prod overlay used `ports: []`, but Compose **appends** an override's list to
    the base file's list, so nothing was removed. Only `ports: !reset []` clears it.
  - **Proof.** Rendered with Compose v5.5.1, the prod config published every internal port on
    `0.0.0.0`: Postgres 5432, Redis 6379 (no password; ARQ jobs are pickled), MinIO 9000/9001,
    MLflow 5000 (no auth), api 8000 and web 3000. Ports published by Docker bypass ufw.
  - **The fix.**
    - The overlay now uses `!reset` for postgres, redis, minio, api, web and mlflow.
    - `scripts/check-compose-ports.sh [ENV_FILE]` renders the merged prod config and fails on any
      published port other than caddy 80/443 (or on `network_mode: host`).
    - CI runs it after `config --quiet`.
  - **Minimum Compose version.** Docker documents no minimum for `!reset`: the docs require
    2.24.4 only for `!override`, and `!reset` exists in compose-go at least since v1.14 (2023) with
    fixes since. Do not rely on a version number — run the script on the server after installing
    or upgrading Docker.
  - **Redis `requirepass` — done 2026-10-10.** Redis holds the pickled ARQ jobs, quotas and
    rate limits; it now requires `REDIS_PASSWORD`, and the script checks the password on the
    server and in every client's `REDIS_URL` (§6 "Redis: password, memory, persistence").
- **web → API wiring (fixed 2026-10-03).** The bug, the fix and the tests:
  - **The bug.** The base compose gave the web container
    `API_BASE_URL: ${API_BASE_URL:-http://api:8000}`, read from `.env`, whose example value is
    `http://localhost:8000`. The rendered prod config confirmed it: with `.env` copied from the
    example, the BFF called `localhost` inside its own container, so the site had no data.
  - **Why deploys passed anyway.** `/api/health` only proves the web process is up, so `deploy.sh`
    reported success.
  - **The fix.**
    - The prod overlay hardcodes the web container's `API_BASE_URL: http://api:8000`. The
      `.env.example` value is marked dev-only.
    - `scripts/check-compose-ports.sh` now also fails unless web's `API_BASE_URL` is
      `http://api:8000`. CI renders it with `API_BASE_URL=http://localhost:8000` set.
  - **A new BFF route, `GET /api/ready`.** It relays the backend's `/health/ready`: 200, or 502
    `backend_unavailable` when unreachable. It touches no database, Redis or tiers, so it answers
    200 on an empty database. A backend 404 is mapped to 502 `backend_ready_not_found`, so that
    only a missing route (an older web image) answers 404.
  - **`deploy.sh`.** After the healthchecks it probes `/api/ready` **inside the web container**
    (`compose exec -T web wget …`, so no DNS or TLS is involved), with `DEPLOY_HEALTHCHECK_ATTEMPTS`
    retries 2 s apart. Each request has a timeout (`wget -T`, `DEPLOY_READY_TIMEOUT_SECONDS`, default
    5), so the whole check is bounded.
    - A failure triggers the existing automatic rollback, with a message naming
      `API_BASE_URL` / the api service.
    - An image without the route (404) fails at once.
  - **`rollback.sh`.** It runs the same probe after the rollback. A restored image without the
    route (404) is a failure since F4: its readiness cannot be verified, and every release with a
    digests file has the route (until then it only got a warning).
  - **Tests.** `scripts/tests/deploy-scripts-test.sh` (stubbed `docker`/`sleep`, run in CI) covers
    6 scenarios: deploy ok, unreachable → rollback to the previous tag, old image; rollback ok,
    unreachable, old image.
- **Backups:** (target: WAL-G to an **off-server** destination, an S3-compatible bucket at an external
  provider or another host, **not chosen yet** (owner); the on-server MinIO bucket is gone. The
  first-launch plan is
  manual: Postgres dumps of `football` and `mlflow` plus a tar of the `mlflow_artifacts` volume,
  encrypted and copied off the server, to be written up in `docs/DEPLOY_VPS.md`, which does not
  exist yet) — then `make backup`, weekly
  `make restore-drill`, backup freshness checks, and Telegram ops alerting when backups are stale
  (owner target: alert if backup is older than 15 minutes).
- **Docs:** update README/deploy docs with required env vars, tag-based release flow, deploy, rollback,
  backup and restore-drill commands.

- **Production has never been launched (owner, 2026-10-03).** There is no production server, no
  production data, and no API-Football polling outside dev.
  - The **first launch is from scratch**: an empty database, and migrations `0001..latest` applied
    by `deploy.sh`.
  - The items below marked *future upgrade only* (pre-migration dumps, `data-report` review, the
    one-time `docker compose down`, `replace-source`, port checks on an already running server) do
    not apply to the first launch.
- **API image carries its migrations (fixed 2026-10-03).**
  - **The bug.** `backend/Dockerfile` copied only `pyproject.toml` and `app/`. `deploy.sh` runs
    `compose run --rm api alembic upgrade head`, and in the built image that failed with
    `FAILED: No 'script_location' key found in configuration.` Every deploy would have stopped at
    the migration step.
  - **The fix.** The image now copies `alembic.ini` and `migrations/`.
  - **CI guard.** The "Docker images build" job runs, in the built image, against a
    `timescale/timescaledb:2.30.2-pg16` service: `alembic heads`, `alembic upgrade head` on the
    empty database, and `alembic current` (it must equal heads). It also runs
    `python -m app.cli data-report --help` and `python -m app.bootstrap --help`.
  - **Other runtime files checked.**
    - Backend: only the user-supplied `--offline-dir` CSVs are read from disk; MLflow artifacts are
      on the `mlflow_artifacts` volume behind the MLflow server; `ml_artifacts/` is unused by code.
    - Frontend: no runtime `fs` reads; locale messages and legal texts are bundled; `public/sw.js`
      and `.next/static` are copied into the standalone image.
  - Root-only `__pycache__`/`*.pyc` patterns in `backend/.dockerignore` became `**/`.

- **Timescale 2.30.2-pg16 and Caddy 2.10 (before the first launch, 2026-10-04).**
  - Both are pinned by `tag@sha256` in the compose files, **and** in every CI copy: both
    `ci.yml` service images, the `caddy validate` step and the `scripts/caddy-smoke.sh` default.
    So CI tests the versions production runs.
  - Doing the Timescale jump now is cheapest: there is no database yet, so no
    `ALTER EXTENSION timescaledb UPDATE`.
  - **Checked locally on 2.30.2:** all 17 migrations up, 17 down, up again. The extension is
    2.30.2, and the hypertables are `odds` and `predictions_live`.
  - **Checked on 2.10.2:** the Caddyfile validates.
  - **Existing databases (dev volumes, any future upgrade).** A new image does not update the
    extension of a database created by an older one: on 2.30.2 a 2.17.2 volume kept `extversion`
    2.17.2.
    - An Alembic revision cannot fix this. `ALTER EXTENSION timescaledb UPDATE` must be the first
      command of its own session; inside a transaction it fails with "cannot be updated after the
      old version has already been loaded" (checked).
    - So `deploy.sh` now runs `update_timescale_extension` after postgres is healthy and **before**
      the migrations. In **every connectable database** it checks `extversion` in one fresh
      `psql -X` session and, where the extension is installed, runs
      `ALTER EXTENSION timescaledb UPDATE` in another. The database that `DATABASE_URL` (Alembic)
      targets is therefore covered whatever its name; today it is the only one with the extension.
      `mlflow`, `postgres` and `template1` have none.
    - On the first launch the extension does not exist yet (migration 0003 creates it at the image
      version), so the step is skipped.
    - Checked on a real 2.17.2 volume: upgraded to 2.30.2; a second run is a no-op NOTICE. The
      stub test covers both paths and the ordering before Alembic.
    - On a dev machine, run the same command by hand, or recreate the volume with
      `docker compose down -v`.
- **Healthchecks bound to the wrong address (fixed 2026-10-04).**
  - **web.** The Next.js standalone server listens on `$HOSTNAME`, which Docker sets to the
    container id. It bound only `172.29.89.10:3000`, so the web healthcheck and deploy.sh's
    `/api/ready` probe (both via `localhost`) were refused: web stayed `unhealthy` in the rc2
    rehearsal, although Caddy reached it over the network.
    - Fix: `ENV HOSTNAME=0.0.0.0` in `frontend/Dockerfile` (an image ENV wins over Docker's runtime
      value).
    - Every probe uses `127.0.0.1`: busybox wget resolves `localhost` to `::1`, which a `0.0.0.0`
      listener does not serve.
    - CI starts the built web image and waits for its own HEALTHCHECK to report healthy.
  - **caddy.** Its healthcheck fetched `http://localhost/healthz`. With a real `PUBLIC_DOMAIN`
    that is `308` to `https://localhost/…`, where Caddy has no certificate, so caddy would stay
    unhealthy and **every deploy on a real server would fail**. The rehearsal used
    `PUBLIC_DOMAIN=localhost` and the CI smoke test uses `:80`; both hid it.
    - Fix: the Caddyfile has an internal `http://:8081` block serving `/healthz` (never published;
      `check-compose-ports.sh` still allows only 80/443), and the healthcheck uses
      `http://127.0.0.1:8081/healthz`.
    - `caddy-smoke.sh` now checks it.
    - Verified with `PUBLIC_DOMAIN=betpulse.example.test`.
- **Caddy never received PUBLIC_DOMAIN (fixed 2026-10-04, found in the rc3 rehearsal).**
  - **The bug.** The Caddyfile's site address is `{$PUBLIC_DOMAIN:localhost}`, but the prod
    overlay gave the caddy service no `environment` and no `env_file`. In the rendered config its
    environment was empty and `printenv PUBLIC_DOMAIN` in the container printed nothing, so Caddy
    always served `localhost`. On a real server it would never have requested a certificate for
    the domain or served requests to it.
  - **Why it was hidden.** The rehearsals used `PUBLIC_DOMAIN=localhost`, and CI's Caddy checks
    passed the value with `docker run -e`.
  - **The fix.**
    - The overlay passes `PUBLIC_DOMAIN: ${PUBLIC_DOMAIN:?…}` to caddy, so an empty value fails
      `compose config` (and every deploy) at once.
    - `scripts/check-compose-ports.sh` fails if caddy gets no `PUBLIC_DOMAIN`.
    - `scripts/caddy-domain-smoke.sh` (CI) starts caddy alone from the real prod config, with no
      `-e`:
      - with `example.test`: `printenv`, the site hosts in Caddy's live config (admin API) and
        `curl --resolve` (308 to `https://example.test/`);
      - with `betpulse.localhost`: `curl --resolve` over HTTPS answers `/healthz`.
      - Why two names: `example.test` makes Caddy go to ACME, which cannot succeed in CI, so it
        has no certificate. Caddy issues `*.localhost` certificates from its internal CA, and with
        the variable missing that handshake fails. Checked both ways locally.
- **API image lacked libgomp1 (fixed 2026-10-04).**
  - **The bug.** LightGBM's wheel links the OpenMP runtime, which `python:3.12-slim` does not ship.
    In the rc2 rehearsal all three workers crash-looped at startup with
    `OSError: libgomp.so.1: cannot open shared object file`, because they import the training code.
    CI did not see it: the tests run on the runner, which has the library, not inside the image.
  - **The fix.** `backend/Dockerfile` installs `libgomp1` (no recommends, apt lists removed).
  - **CI guard.** The "Docker images build" job now runs
    `python -c "import lightgbm, app.main, app.workers.arq_app"` inside the built image.
  - **Checked locally.** The imports pass, a 3-round LightGBM training runs, and the process still
    runs as `appuser`.
- **Placeholder secrets from `.env.example` (security, fixed 2026-10-04).**
  - **The bug.** Docker Compose reads an `env_file` line `KEY=   # note` (empty value, inline
    comment) as the value `# note`.
    - The rc2 dress rehearsal created the first admin with
      `ADMIN_PASSWORD = "# leave empty to auto-generate a one-time password"`, a string published in
      the repository. Whoever logs in first can take the account over by changing that password.
    - The rendered prod config showed the same for `BACKUP_ENCRYPTION_PUBLIC_KEY`,
      `TELEGRAM_ALERT_CHAT_ID`, both VAPID keys, and, when left empty, `SECRET_KEY`,
      `DATA_ENCRYPTION_KEY` and `PUBLIC_DOMAIN`.
    - The old `SECRET_KEY` hint is 49 characters long, so it passed the old "at least 32 characters"
      check: JWTs would have been signed with a public string.
  - **The fixes.**
    - **`.env.example`** has no inline comments. All 37 were moved onto their own lines, and the file
      says why at the top. `tests/test_env_placeholders.py` fails on any new one.
    - **`scripts/check-compose-ports.sh`** (CI, and on the server) now also fails when any service
      environment value starts with `#`. It names the key only, never the value.
    - **Production startup** (`Settings`) refuses placeholder or weak secrets:
      - `SECRET_KEY`: placeholder (empty, `#…`, a known stand-in, anything containing "example",
        three or fewer distinct characters), shorter than 32 characters, or under 128 bits by a
        Shannon estimate;
      - `DATA_ENCRYPTION_KEY`: not 32 bytes of hex, or under 128 bits;
      - `ADMIN_PASSWORD`, if set: a placeholder, shorter than 12 characters, or under 40 bits.
    - **`create-admin` in production** requires an explicit strong `ADMIN_PASSWORD`. It refuses an
      empty, placeholder (`#…`), documented-example, short or predictable value with exit 2 and
      creates nothing. In development an empty value still generates a one-time password.
    - **Error output.** In production a placeholder `ADMIN_PASSWORD` is rejected by the settings
      validation itself. `python -m app.bootstrap create-admin` catches that and exits **2**,
      printing only the error messages.
    - **Inputs are never echoed.** `Settings` sets `hide_input_in_errors=True`. Before this,
      pydantic printed `input_value={…}` with the given settings, and its tail showed the
      password. That affected any misconfigured production start of the api and workers, which
      could leak secrets into the logs.
    - Every comment line of `.env.example` is tested to be classified as a placeholder.
    - **Patterns that fool a Shannon estimate are rejected too** (review, CWE-330):
      `"abcdefghijklmnop" * 2` (≈128 bits by Shannon) and `"0123456789abcdef" * 4` (valid hex,
      ≈256 bits) used to pass. Now also refused:
      - a value made of a repeated block;
      - a value of 32+ characters with fewer than 10 distinct characters;
      - any 8-character run stepping by ±1 (`abcdefgh`, `01234567`).
      Measured on 100 000 values each: `secrets.token_hex(32)` and `secrets.token_urlsafe(32)`
      were never rejected; the test uses 10 000 seeded values, so it is deterministic.
  - **Runbook requirement.** Set `ADMIN_PASSWORD` explicitly in the server `.env` before
    `create-admin`, and generate `SECRET_KEY` / `DATA_ENCRYPTION_KEY` with `openssl rand -hex 32`.
- **MinIO removed; MLflow serves its own artifacts (fixed 2026-10-04).**
  - **Why.** The dress rehearsal (`v0.0.1-rc1`, real `deploy.sh`) stopped at
    `compose up -d postgres redis minio` with `pull access denied for minio/minio`. MinIO has been
    source-only since 2025-10: `minio/minio` and `minio/mc` are gone from Docker Hub, quay.io
    refuses anonymous pulls, and dl.min.io answers `410 Gone`.
  - **Scope check.** S3 was used only by MLflow artifacts (server side, via `--serve-artifacts`) and
    a `backups` bucket that no code used. No backend code imported `boto3`, and
    `MLFLOW_ARTIFACT_ROOT` was unused.
  - **The change.**
    - The `minio` and `createbuckets` services, the `mc` healthcheck, the `S3_*` /
      `MLFLOW_ARTIFACT_ROOT` variables and `boto3` (backend and MLflow image) are removed.
    - The MLflow server stores artifacts on the `mlflow_artifacts` named volume:
      `--serve-artifacts --artifacts-destination /mlflow/artifacts
      --default-artifact-root mlflow-artifacts:/`. Clients upload and download over its HTTP API.
    - `deploy.sh` starts `postgres redis` first.
  - **Second finding, fixed in the same PR.** MLflow 3 rejects any Host header outside its allow
    list (`403 Invalid Host header - possible DNS rebinding attack detected`). The default list
    allows localhost and private IPs, but not the service name, so every training run from the
    api/workers (`http://mlflow:5000`) would have failed. The server now runs with
    `--allowed-hosts mlflow,mlflow:5000,localhost,localhost:*,127.0.0.1:*`.
  - **Verified locally** (postgres + the new MLflow from the prod config, the `v0.0.1-rc1` api image):
    - `log_training_run` logged a run;
    - the files landed on the volume (`/mlflow/artifacts/1/<run>/artifacts/model/model.joblib`,
      `feature_schema.json`);
    - the model and schema loaded back through the proxy and matched;
    - after `rm` + `up` of the MLflow container the model still loaded;
    - through a socat forwarder on `127.0.0.1:5001` the UI and the API answered 200.
  - **Backups.** The `mlflow_artifacts` volume must be backed up together with the `football` and
    `mlflow` databases (volume tar, encrypted off-server copy) — see the VPS runbook.
  - **Dev databases** that still hold experiments with `s3://` artifact locations must be recreated
    (`docker compose down -v`). Production never ran, so nothing there is affected.
  - **Image pinning and checks.**
    - Third-party images are pinned by `tag@sha256` in the compose files and the Dockerfile bases.
    - Dependabot covers `docker-compose` (`/infra`) and the MLflow Dockerfile.
    - `.github/workflows/upstream-images.yml` runs weekly (Mondays 05:17 UTC) and on demand. It
      pulls every non-betpulse image of the rendered prod config and every Dockerfile base image,
      and rebuilds the MLflow image, so a vanished upstream image is caught before a deploy needs
      it.

### Release images and digests (F2 + ER-H-09, 2026-10-05)

- **Three images per release.** `release.yml` publishes `ghcr.io/<owner>/betpulse-api`,
  `betpulse-web` and `betpulse-mlflow` under the same version tag.
  - **`latest`** moves only for a stable version (`vX.Y.Z`); a pre-release (`-rc1` …) never
    gets it (`scripts/release-version.sh`).
  - **A version is published once.** The run fails if the GitHub Release or any of the three
    tags already exists (GHCR tags are mutable). A failed or repeated pre-release gets a new rc
    number.
- **Digests.** The run writes `release-<version>.digests` from the top-level (index) digest of
  each push:
  ```
  RELEASE_VERSION=v1.2.3
  API_IMAGE_DIGEST=sha256:…
  WEB_IMAGE_DIGEST=sha256:…
  MLFLOW_IMAGE_DIGEST=sha256:…
  ```
  It is attached to the **GitHub Release** of that version (pre-releases are marked as such),
  shown in the job summary, and uploaded as a workflow artifact. The repository is public, so the
  server fetches it without credentials:
  `curl -fLO https://github.com/desn1k/betpulse/releases/download/<version>/release-<version>.digests`
  (or `gh release download <version>` / `scp` from a workstation).
- **Deploy and rollback by digest.**
  - `IMAGE_TAG=<version> RELEASE_DIGESTS=release-<version>.digests scripts/deploy.sh`. The script
    checks that the file is for that version and that every digest is `sha256:<64 hex>`, pulls
    and restarts `api`, the three workers, `web` **and `mlflow`**, and keeps a copy as
    `.release/<version>.digests`.
  - The automatic rollback (F4) runs `scripts/rollback.sh` itself, non-interactively, with the
    previous release's stored digests: pull and up by digest, then wait for every service and
    `/api/ready`. Manual and automatic rollback are one code path. Without stored digests it does
    not roll back blindly by tag and says how to run `scripts/rollback.sh`.
  - **`deploy.sh` exit codes:**
    - `0` deployed, healthy and ready;
    - `1` refused before anything changed (no `.env`, bad `IMAGE_TAG`, bad or missing digests
      file);
    - `2` deployment failed, the previous release was restored and is healthy and ready;
    - `3` deployment failed **and the automatic rollback failed too: the site may be down**;
    - `4` deployment failed, no automatic rollback (first deploy on this server, or no stored
      digests for the previous release).
    Codes 2–4 also say whether migrations ran, or only the TimescaleDB extension was updated:
    the database schema is never rolled back (ER-H-08). Before F4 a failure exited with the failing command's own code (any number).
  - The tag in an image reference is decorative: Docker pulls `repo:tag@digest` by the digest
    even when the tag does not exist (checked 2026-10-05).
  - `IMAGE_TAG=<version> scripts/rollback.sh` uses `.release/<version>.digests`, or
    `RELEASE_DIGESTS` for a release never deployed on this server.
  - **A successful rollback records its release as the deployed one** (fixed 2026-10-06; before,
    `rollback.sh` wrote nothing). It stores the digests (when `RELEASE_DIGESTS` points outside
    `.release/`) and then `last-successful-image-tag`, each through a temporary file renamed into
    place; `deploy.sh` records a successful deploy the same way (`record_deployed_release` in
    `scripts/release-digests.sh`). So after a manual rollback from B to A, `prod-compose.sh` runs A
    and the next failed deploy rolls back automatically to A, not to the rejected B (and a failed
    redeploy of B gets exit 2, not 4). A failed rollback changes nothing in `.release/`.
  - **One deploy or rollback at a time** (2026-10-06). `deploy.sh` and `rollback.sh` hold one lock,
    `.release/.deploy.lock` (a directory with the owner's PID; created with `.release/` itself on
    an empty server), from before the first docker call until they exit. A second run is refused
    with **exit 1** before any docker call: "another deploy or rollback is running (pid N)".
    Only the process that took the lock removes it, on success, failure, Ctrl+C or SIGTERM; the
    automatic rollback runs under `deploy.sh`'s lock (`BETPULSE_RELEASE_LOCK_PID`, accepted only
    from that parent: set by hand, the run is refused).
    - **Stale lock** (the owner was killed with `kill -9`, or the host crashed): the run is refused
      with exit 1 and says the lock is stale. It is never removed automatically. Check that no
      `deploy.sh`/`rollback.sh` is running (`pgrep -af 'scripts/(deploy|rollback)\.sh'` prints nothing), then
      `rm -rf .release/.deploy.lock` in the repository root and run again.
    - **Not covered:** `scripts/prod-compose.sh` (and `make ps-prod`/`logs-prod`/`config-prod`).
      Read-only commands are harmless, but never run `prod-compose.sh up`/`down`/`pull` while a
      deploy or rollback is running.
- **Day-to-day compose commands.** `scripts/prod-compose.sh <args>` (and `make ps-prod`,
  `logs-prod`, `config-prod`) runs `docker compose` on the prod config with the tag and digests
  of the release deployed here (`.release/`); `make deploy`/`rollback` pass `RELEASE_DIGESTS`.
- **Prod compose requires both.** Each app image is `…:${IMAGE_TAG:?}@${<X>_IMAGE_DIGEST:?}`;
  without a tag or a digest `docker compose config` fails. `build: !reset null` removes the base
  file's `build` (a plain `build: null` is ignored by the merge, like `ports: []`).
  `scripts/check-compose-ports.sh` fails on any image without `@sha256:` and on any service built
  on the server; CI proves both checks fail on a bad override.
- **GHCR access on the server.** A new package such as `betpulse-mlflow` is **private by
  default**. The server's read-only GHCR token (`read:packages`) must have access to **all three
  packages** (package settings → manage access), or the pull of the missing one fails.
- **Not verified until the first published release with this workflow** (v0.0.1-rc4, the owner
  runs it): the GitHub Release asset, `docker pull <image>@<digest>` for all three images, no
  `latest` on an rc, and a deploy and a rollback by digest on the local stack.

### Pre-deploy manual checklist

Run before **every** deploy, including the first one:

- [ ] `bash scripts/check-compose-ports.sh .env` on the server prints `OK`: only caddy 80/443 are
  published, and web reaches the API at `http://api:8000`.
- [ ] After the deploy, `docker ps --format '{{.Names}}\t{{.Ports}}'` and `sudo ss -tlnp` show
  nothing but 22/80/443 listening on public addresses.
  - If 5432, 6379, 9000/9001, 5000, 8000 or 3000 are public, close everything except 22/80/443
    **in the provider firewall** (ufw does not filter Docker-published ports).

*Future upgrade only* (a server with data; none exists yet). Run before deploying a release that
contains data migrations, e.g. 0014/0015 (HI-2) and 0017:

- [ ] On the server, `docker compose … exec api python -m app.cli data-report --json` (read-only;
  the host has no Python for `make data-report`): every `duplicate_fixture`
  error and every `non_canonical_season` warning is a row the migration may STOP on. Resolve them
  first (or accept the STOP and fix then) — the migrations never merge.
- [ ] Take a **manual `pg_dump`** of the database and keep it off the host (automated backups are not
  in place yet), e.g. `docker compose exec postgres pg_dump -U football -Fc football >
  betpulse-pre-0015.dump`.
- [ ] Migration **0017** (single champion): on the server, `SELECT method, version FROM
  model_registry WHERE status = 'champion'` must return at most one row; otherwise the upgrade STOPs
  with the list. Demote the extras in Admin → Models first.

**Migration 0018 (LLM cache per language, `llm_generations` journal; ER-H-02) and image
rollback.** Not compatible with rolling the images back to a release before 0018 (ER-H-08):
the old code looks the cache up by `(fixture_id, model)` with `scalar_one_or_none()`, which raises
`MultipleResultsFound` once a fixture has analyses in two languages, and its upsert names
`uq_llm_analysis_fixture_model`, which no longer exists, so every new generation fails. LLM
analysis answers 500 there; the rest of the site works, and the old spend page reads the cache rows
(it under-reports again). Before rolling back to a pre-0018 release, either:
- turn LLM analysis off in Admin → LLM (`is_enabled = false`; the endpoint then answers
  `disabled` without touching the cache), or
- downgrade the schema first (below), then roll the images back.

**The automatic rollback does not do this** (CodeRabbit on #117, accepted as documented): when
`deploy.sh` fails after 0018 ran and restores a pre-0018 release itself (exit 2), LLM generation
stays enabled. On a cache miss the old code calls the provider and adds the tokens to the daily
Redis budget, then its upsert fails: paid calls, error responses, and no journal row. **Not
reachable in production**: the first VPS launch starts at a release with 0018, so no pre-0018
release is ever deployed there to roll back to. It matters only where a pre-0018 release ran
before, i.e. the local rehearsal of rc5 → rc6: disable LLM there before any rollback drill.
**Not verified in rc6:** the rehearsal has no LLM provider configured, so the expected failure
of a pre-0018 image's LLM generation on the 0018 schema was never provoked; it stays open.

- [ ] **Before any downgrade of 0018**, dump the journal (the only spend history) and the cache, and
  keep the file off the host:
  `scripts/prod-compose.sh exec -T postgres pg_dump -U football -Fc -t llm_generations -t
  llm_analyses football > betpulse-llm-pre-downgrade-0018-$(date -u +%Y%m%dT%H%M%SZ).dump`
  Then `scripts/prod-compose.sh run --rm api alembic downgrade 0017_single_champion`. The log
  shows `postgres NOTICE: 0018 downgrade: N llm_analyses rows deleted, M llm_generations rows
  dropped`: the newest analysis per `(fixture_id, model)` is kept (`created_at DESC, id DESC`), the
  others and the whole journal are gone. Restore the journal from the dump only after upgrading to
  0018 again (`pg_restore --data-only -t llm_generations`), after the back-fill rows are removed.

### Post-deploy manual checklist

Run after the **first** deploy of a new image (and after any change to the frontend build/trace
config) — CI only proves the image builds (`Docker images build`), not that it serves:

- [ ] From the real frontend image: `GET /api/health`, `GET /` and `GET /performance` all respond
  `200` (the standalone trace excludes `typescript`; a missing runtime module would surface here).

### rc3 dress rehearsal (2026-10-04)

`v0.0.1-rc3` (main `1b0a5dd`) was deployed from scratch with `scripts/deploy.sh` on Docker
Desktop, with empty volumes, `ENVIRONMENT=production`, `PUBLIC_DOMAIN=localhost` and generated
secrets.
- **Passed:**
  - all 9 services healthy; `check-compose-ports.sh` OK; Alembic at head on Timescale 2.30.2;
  - `/api/health`, `/api/ready`, and `/` and `/performance` in ru and en through Caddy;
  - `create-admin` and admin login;
  - one job on each queue: batch and ml ran theirs, all three workers have a heartbeat;
  - an MLflow run from `worker-ml`, its artifact on the volume and loaded back through the proxy,
    and the UI through a socat forwarder.
- **Rollback checks:**
  - a missing tag failed at pull and changed nothing;
  - a deliberate deploy of rc2 (no libgomp) failed and rolled back to rc3;
  - the database was unchanged afterwards (Alembic head, admin login), and `rollback.sh` passed.
- **Findings, each its own PR:**
  - F1: caddy got no `PUBLIC_DOMAIN` (fixed above);
  - F2: the MLflow image is built on the server and never rebuilt by `deploy.sh` — fixed
    together with **ER-H-09** (see "Release images and digests" below);
  - F3: without `API_FOOTBALL_KEY` the live poll calls the API every minute and fails with 403 —
    fixed 2026-10-05: `live_provider_configured()` (`app/services/live/provider.py`) is the one
    check; without a key `worker-realtime` logs one warning at start-up and starts no poll, and a
    poll left in Redis by an older release returns without a request and without rescheduling.
    To enable live: set the key, restart `worker-realtime`. Seen live in the rc5 rehearsal (below);
  - F4: `deploy.sh`'s automatic rollback does not wait for health or `/api/ready` — fixed
    2026-10-05: it runs `rollback.sh` and reports the outcome by exit code (see "Release images
    and digests"). Provoked live in the rc5 rehearsal (below); still a first-VPS item.

### rc5 rehearsal (2026-10-06)

`v0.0.1-rc5` (main `2210cf8`, release run 37506302174) was deployed on the local Docker Desktop
stack over rc4 with its data (not from scratch), in the rehearsal clone that holds `.release/`.
- **Release:** a pre-release with `release-v0.0.1-rc5.digests`; the GHCR rc5 tags match the file;
  `latest` did not move (api/web unchanged, mlflow has none).
- **Deploy rc4 → rc5:** exit 0; all six app containers on the rc5 digests; `last-successful` =
  rc5.
- **F3 live:** exactly one "Live polling is disabled" line at start-up and again after a restart
  of `worker-realtime`; over 4 minutes no API-Football request and no 403. The one
  `poll_live_task` rc4 had left in Redis ran once, returned 0 without a request and did not
  reschedule.
- **F4 drill** (a redeploy of rc4 as the "failing" release, `api` stopped from a second process):
  - `api` stopped once it was healthy on rc4: `/api/ready` failed → rolled back to rc5, **exit 2**,
    "healthy and ready", schema note ("migrations of v0.0.1-rc4 already ran"), state still rc5;
  - `api` stopped in a loop: **exit 3**, "AUTOMATIC ROLLBACK TO v0.0.1-rc5 FAILED", schema note;
    then `rollback.sh` to rc5 by hand: exit 0, `/api/ready` 200 through Caddy.
  - **Observation:** in that exit-3 run both the deploy and the rollback failed in
    `compose up` ("dependency failed to start: container betpulse-api-1 exited (137)": `web`
    waits for a healthy `api`), not in the health or `/api/ready` wait. The exit-3 path through a
    failed wait was therefore not exercised live; it is covered by the script tests only (2b, 2c).
- **Lock:** a `rollback.sh` started while a deploy ran: exit 1, "another deploy or rollback is
  running (pid N)", no docker call from it; the deploy finished with exit 0.
- **State after a manual rollback:** `rollback.sh` rc5 → rc4: exit 0, `last-successful` = rc4,
  `prod-compose.sh config` on the rc4 digests; then rc5 deployed again (exit 0).
- `.release/.deploy.lock` was gone after every step; no volume was removed.

### rc6 rehearsal (2026-10-08)

`v0.0.1-rc6` (main `17d508a`, release run 37807788266) was deployed on the local Docker Desktop
stack over rc5 **with data**, in the rehearsal clone that holds `.release/`. Checklist:
`rc6-checklist.md` in that clone; the agent ran the release, deploy and drills (owner's one-time
exception), the owner ran the browser checks.
- **Seed before the run.** rc5's database was empty. A one-off container from the rc5 api
  image (by digest, `ENVIRONMENT=development`, on the stack network) ran `bootstrap-history`
  for EPL 2023-24 from the committed CSV (10 finished matches) and `train` (Elo, Glicko-2,
  Dixon-Coles, market; LightGBM and consensus skipped below 200 samples); a test user was
  registered through the production API inside the api container. The stack's own production
  image still refused football-data (exit 2), so the licence gate was untouched. These
  football-data rows exist only in the rehearsal database. Helper scripts (untracked, in the
  clone): `seed-rehearsal.sh`, `verify-rehearsal.sh` (read-only summary),
  `admin-setup-rehearsal.sh` (admin password change, TOTP, tier grants through the production
  API — the workaround for F10), `watch-pause-api.sh` (exit-3 drill).
- **Release:** a pre-release on `17d508a` with `release-v0.0.1-rc6.digests`; the GHCR rc6 tags
  match the file; `latest` did not move (api, web unchanged; mlflow has none).
- **Deploy rc5 → rc6:** exit 0; exactly one `Running upgrade 0017_single_champion ->
  0018_llm_generations`; no NOTICE/WARNING besides psql's "extension timescaledb is already
  installed"; one unique constraint left on `llm_analyses`
  (`uq_llm_analysis_fixture_model_language`); `llm_generations` exists with 0 rows (the
  back-fill had nothing to copy: `llm_analyses` was empty); all six app containers on the rc6
  digests.
- **Browser (owner):** F6 (all steps: one tab for 5 minutes keeps `matches_remaining` = 2, the
  same match again is free, the fourth match shows the limit), F8 (card and stale note while
  `api` was stopped; note gone without a reload once it was back), F9 (Pro bars through
  reloads and after 20+ minutes, no "session expired"; logout in another tab signs the match tab
  out quietly), and the home-page counter stable over many reloads (on rc5 it jumped between
  the guest and the user value: the first list request went out before the session was
  restored — the F9 page-load bug).
- **Drills** (LLM confirmed off first: `llm_config` has no row):

  | Drill | Result |
  |---|---|
  | exit 2: redeploy rc5 over the 0018 schema | rc5's `alembic upgrade head` failed (`Can't locate revision identified by '0018_llm_generations'`), automatic rollback to rc6 "healthy and ready", **exit 2**; rc5's app never started. The schema note says "the migrations of v0.0.1-rc5 already ran" because the flag is set before Alembic runs — expected wording |
  | exit 3 through the wait, not `compose up` | same failing deploy; `watch-pause-api.sh` paused `api` on the rollback's last `compose up` line (`Container betpulse-api-1 Healthy`, ~2 s window); `Service api did not become healthy after rollback.`, `AUTOMATIC ROLLBACK TO v0.0.1-rc6 FAILED`, **exit 3**, no `dependency failed`. First live exit 3 through the wait (rc5's only reached `compose up`). Unpause + `rollback.sh` rc6: exit 0; `.release/` unchanged by the failed rollback |
  | manual rc6 → rc5 on the 0018 schema | exit 0, rc5 by digest, schema stays 0018; `/api/ready`, `/` and match pages 200 |
  | back to rc6 | exit 0, no migration ran |

- **Not verified in rc6:** LLM generation by a pre-0018 image on the 0018 schema (no LLM
  provider is configured, so the expected failure could not be provoked; still open, §9i).
  Also skipped by the owner: the `disabled` answer through the API (needs Expert: the tier
  check runs first and the seeded matches have no LLM rank), the lock drill (unchanged since
  rc5) and the 0018 downgrade drill.
- `.release/.deploy.lock` was gone after every step; no volume was removed; the stack is left on
  rc6.
- **Findings:** F10–F16 (§9m, "Found in the rc6 rehearsal").
- **Checklist corrections for the next rehearsal:** a Pro user sees three method bars, not four
  (the market is not a bar, §9m); with `MSYS2_ARG_CONV_EXCL=/dev/null` Git Bash's Windows curl
  cannot write to `/dev/null` (exit 23): use `-o NUL`; schannel curl needs `--ssl-no-revoke`
  with Caddy's local root (`--cacert`, chain still verified); `tail -F` polls once a second,
  too slow for the ~2 s exit-3 window: the watcher uses `-s 0.1`; on Docker Desktop the browser
  and curl share one guest identity (`172.29.89.1`), so a guest quota spent earlier that UTC
  day blocks the F6 check; `docker run --env-file` does not expand `${VAR}` inside `.env`
  values (compose does), which broke the first seed attempt.

### Redis move on the rehearsal stack (2026-10-10)

Run by the agent with the owner's permission, from the scratch clone checked out at the PR
branch, on the running rc6 stack (rc6 images: the code from before this PR, i.e. what a rollback
runs). Log: `rehearsal-logs/redis-aof-20261010T070048Z.log` in the clone.
1. Postgres dumps first (`dumps/pre-redis-aof-{football,mlflow}-20261010T070048Z.dump`); Redis
   before: RDB only, no password, 3 heartbeat keys plus a `tier:guest` cache key; three markers
   added (no TTL, a 24 h TTL, a 3-item list).
2. `REDIS_PASSWORD` added to the clone's `.env` (the previous file kept beside it).
3. `IMAGE_TAG=v0.0.1-rc6 … scripts/deploy.sh` → **exit 1**, "Redis runs without AOF and holds 7
   keys", the procedure printed, nothing changed.
4. `scripts/redis-enable-aof.sh` → BGSAVE ok, dump copied to `.release/redis/` (`sha256sum -c`
   OK), AOF on, rewrite ok.
5. The same deploy → exit 0. Redis: anonymous `PING` → `NOAUTH`; `appendonly yes`, `maxmemory
   268435456`, `noeviction`; all three markers back with their TTL (the `tier:guest` cache key
   had expired, as it does). api and the three workers (rc6 images) healthy with the password in
   `REDIS_URL`, no authentication error in their logs; `/api/ready` and `/api/matches` 200.

The clone stays on the PR branch with Redis on AOF and a password; the rc7 rehearsal deploys from
`main` after the merge, and deploy.sh no longer refuses there.

### v0.0.1-rc7 (planned)

The owner rehearses it on the local stack after ER2-01; checklist by the agent. Besides the
rc6 items that still apply (deploy over data, migration log, drills), it must check by hand:
- **Migration 0019** in the deploy log (one `Running upgrade 0018_llm_generations ->
  0019_credentials_changed_at`), `credentials_changed_at` NULL for every existing user, and
  nobody signed out by the deploy (an open signed-in tab keeps working).
- **F10 on the real stack:** the stand's admin (password changed, TOTP on in rc6) signs in with
  the code step; Admin → Users grants and removes a tier through the site; the full first
  sign-in on a clean database (separate compose project): initial password → forced change → QR
  in an authenticator app (the QR key equals the text key) → code → admin panel → sign out →
  sign in with a code.
- **reset-2fa** through `scripts/prod-compose.sh run --rm api python -m app.cli reset-2fa …`:
  one output line, no secret; the admin signs in with the password alone and is led to set up
  TOTP again; an open tab of that admin signs out on its next request.
- **Limits:** the sixth wrong password change or TOTP code in a row says "Too many attempts".
- **Password change with TOTP on:** the form asks for the code, not the sign-in screen.
- **ER2-01 across browsers:** change the password in browser A; an open tab in browser B signs
  out with "session expired" on its next action; a second tab in browser A keeps working.
- **Admin disable/enable:** disable a signed-in test user, enable them again: their open tab is
  signed out and does not come back to life.
- No "Sign in" flash on fast reloads (F14); a regular user can turn TOTP on and off with a code.
- **F5 on Docker Desktop:** the admin system health page shows `client_ip` **degraded** (every
  request from the host arrives as the gateway there; expected, the server check decides F5),
  and the api log has one "inside our own Docker network" warning per process. The stack is
  already on the dual-stack network (§9m F5), so deploy.sh does not refuse.
- **F7:** a match page and the list keep working through normal use (no 429 from clicking
  filters or leaving tabs open); with `RATE_LIMIT_MATCH_LIST_PER_WINDOW=3` set for a moment
  (restart api), the list shows "Too many requests…" on a first load and keeps the cards with the
  stale note on a refetch; with `RATE_LIMIT_REFRESH_PER_MINUTE=1`, a reload or two keeps you
  signed in ("Can't renew your session — retrying…") and renews after the minute. Put both back.
- **Redis (2026-10-10).** The local stack already moved (above). For any stack still on an
  earlier Redis, before the deploy, in this order: **`BGSAVE` and an off-container copy of the
  dump** — `scripts/redis-enable-aof.sh` does both first (`.release/redis/dump-<UTC>.rdb` and
  its `.sha256`), then turns AOF on — after `REDIS_PASSWORD` is in `.env`; then the deploy.
  After the deploy: anonymous `redis-cli ping` in the redis container answers `NOAUTH`; Admin →
  System shows **`redis_memory` ok** with the used share of 256 MB; all three workers healthy;
  `scripts/diagnose-client-ip.sh` still lists guest quota keys (it authenticates now).
- **ER2-05:** nothing to click; the deploy log shows no `canceling statement due to statement
  timeout`, and the backtester and an admin model action still finish.

### Launch blockers before the first VPS run

The site is not opened before all are done (F5: done in code, not yet verified on a server).

**Closed trial run before F5 is verified (owner, 2026-10-09).** A trial run on a VPS is
acceptable **only with ports 80/443 restricted to the owner's own addresses (IPv4 and IPv6) in
the provider firewall** (ufw does not filter Docker-published ports) until
`scripts/diagnose-client-ip.sh` shows real IPv4 and IPv6 client addresses there. Before that,
every IPv6 client may reach the API as the bridge gateway, so per-IP limits, guest quotas and
the refresh replay's subnet binding (§6 "Refresh replay") would not tell clients apart; nobody
else may reach the site in that state.
- **F5** — IPv6 / userland-proxy identity collapse (§9m). **Fixed in code 2026-10-09; verified
  on a server: not yet** (first-VPS checklist below).
- ~~**F7**~~ — done 2026-10-09 (§9m).
- ~~**ER2-05**~~ — done 2026-10-10: API-only `statement_timeout` 15 s and `lock_timeout` 5 s
  (§6 "Database timeouts").
- ~~**Redis `requirepass`**~~ — done 2026-10-10 (§6 "Redis: password, memory, persistence").
- ~~**Redis persistence and memory policy**~~ — done 2026-10-10: AOF (`everysec`) plus RDB,
  `maxmemory 256mb` with `noeviction` (every quota, rate-limit and ARQ key has a TTL, so no
  eviction policy could spare them), the admin health component `redis_memory` (degraded above
  80 %), and the guarded one-time move to AOF (§6; procedure in the outline below, item 6).
- ~~**`docs/DEPLOY_VPS.md`**~~ — written 2026-10-10 (in Russian; the owner's runbook). It
  absorbs the list below, and has the section **"Lost authenticator"** (§6.3 there): `scripts/prod-compose.sh run --rm api python -m app.cli reset-2fa
  --email <address> [--require-password-change]` (needs shell access to the server; prints one
  line, never the secret; turns TOTP off, ends every session, audits
  `auth.2fa.reset_by_operator`), then the admin signs in with the password alone and is led to
  set up TOTP again in the UI. Use `--require-password-change` when the device may have been
  stolen together with the password.

**`docs/DEPLOY_VPS.md` outline (agreed with the owner 2026-10-09; written 2026-10-10 — the file
is now the source; this outline is kept as background):**
1. **Server.** Ubuntu LTS; Docker Engine ≥ 27 with the compose plugin (ip6tables is on by
   default). `/etc/docker/daemon.json`: `{"userland-proxy": false}` **recommended, not
   required** — nothing in the repository needs the proxy:
   - unaffected (inside containers, no published port): every healthcheck (api, mlflow,
     web, caddy `:8081`), deploy.sh/rollback.sh's `/api/ready` (`exec web wget 127.0.0.1`),
     `caddy-smoke.sh` (`exec wget` in caddy), `diagnose-client-ip.sh` (`exec netstat`,
     `exec redis-cli`);
   - IPv4 loopback to a published port still works through NAT (Docker sets
     `route_localnet`): the MLflow socat forwarder published on `127.0.0.1:5001` with
     `ssh -L 5001:127.0.0.1:5001`, and `curl --resolve <domain>:443:127.0.0.1` on the host
     (`caddy-domain-smoke.sh` does that in CI, where the proxy is on);
   - **IPv6 loopback (`::1`) to a published port stops working** — use `127.0.0.1`
     (`curl -4`), never `localhost` where it resolves to `::1` first;
   - gain: if the network ever loses IPv6 again, IPv6 clients get "connection refused"
     instead of silently sharing the gateway's identity. Verify the setting with the
     diagnosis (no `docker-proxy` for 80/443).
   With forwarding on, `accept_ra=2` on the uplink if its IPv6 route comes from router
   advertisements. The deploy user, SSH keys, unattended upgrades.
2. **Provider firewall.** 22 from the owner's addresses only. **Closed trial: 80/443 from the
   owner's IPv4 and IPv6 addresses only.** ufw does not filter Docker-published ports.
3. **DNS.** A and AAAA (only A under the IPv4-only stopgap, §9m F5 option 3).
4. **Certificates during the closed trial.** With 80/443 closed to everyone else, ACME HTTP-01
   and TLS-ALPN-01 both fail (Let's Encrypt must reach the server). **Chosen: Caddy's internal
   CA for the trial.** `PUBLIC_DOMAIN` stays the real domain, so the certificate names the real
   host; a trial switch (designed and tested in the DEPLOY_VPS PR; e.g. the global option
   `local_certs` set through an environment placeholder in the Caddyfile, off by default and
   reported by `check-compose-ports.sh`) makes Caddy issue it from its own CA. The browser warning
   is expected; the owner may import Caddy's root (`/data/caddy/pki/authorities/local/root.crt`
   in the caddy volume, as `caddy-root.crt` in the rehearsals) to silence it. HSTS
   (`max-age=31536000; includeSubDomains; preload`) is sent: harmless over the trial (a browser
   ignores HSTS on a connection with a certificate error, and a trusted local root is a valid
   connection), but never submit the domain to the preload list during it. **Opening the site:**
   turn the switch off, open 80/443 to everyone, restart caddy; it obtains the public
   certificate on the first request and the checklist's ACME item is checked then.
   Alternatives: **DNS-01** — a public certificate while closed, but needs a Caddy build with the
   DNS provider's module and an API token on the server; **open 80 for issuance only** — a public
   certificate, but the site is briefly reachable by everyone; **a self-signed certificate** —
   like the internal CA with more manual steps.
5. **Install.** Clone, `.env` (`chmod 600`, owner the deploy user), `REDIS_PASSWORD` from
   `openssl rand -hex 32` (letters and digits only), `docker login ghcr.io` with a read-only
   token, `scripts/check-compose-ports.sh`.
6. **Deploy.** `IMAGE_TAG=<tag> RELEASE_DIGESTS=release-<tag>.digests scripts/deploy.sh`
   (explicit tag). First deploy on an empty server: the network is created, no `down` needed.
   **Network changes later (like F5):** deploy.sh/rollback.sh refuse with the one-time
   procedure — `scripts/prod-compose.sh down` (never `-v`), then the same command again.
   **Redis without AOF (any stack that ran a release before 2026-10-10):** deploy.sh refuses
   (exit 1, nothing changed; Redis would start empty). One-time procedure, the site stays up:
   1. add `REDIS_PASSWORD` to `.env` (above);
   2. `scripts/redis-enable-aof.sh` — it starts with **BGSAVE** (awaited) and an **off-container
      copy of the dump** (`.release/redis/dump-<UTC>.rdb` plus `.sha256`; copy it off the server
      too), then turns AOF on live and waits for the rewrite; a stopped Redis is converted in a
      temporary container;
   3. the same deploy command: Redis restarts with the password and AOF and loads every key.
   Verified on the rehearsal stack 2026-10-10 (rc6 images, 7 keys incl. TTLs kept; §9i "Redis
   move on the rehearsal stack"). **Restoring that dump** (only if the move went wrong; not
   rehearsed yet — test it in the DEPLOY_VPS PR): stop redis, put the copy into the volume as
   `dump.rdb` and remove `appendonlydir`, run `scripts/redis-enable-aof.sh` (the stopped-Redis
   path), start redis.
7. **F5 check:** `scripts/diagnose-client-ip.sh`, what each line should say, and what to do on
   "OUR NETWORK" or "IPv6 real client seen: NO" (the IPv4-only stopgap, §9m F5).
8. **F13 B check** (first-VPS checklist below), after 7 passes.
9. The rest of the first-VPS checklist below (sockets, MLflow tunnel, Web Push, rollbacks).
10. **Lost authenticator** (above).
11. **Rollback**, including a rollback across a network change.
12. **Opening the site:** only when every launch blocker above is done and 7 and 8 passed;
    switch to the public certificate (4).

### First VPS launch: items no rehearsal could verify

Check these on the server during the first launch (runbook: `docs/DEPLOY_VPS.md` section 7,
which `backend/tests/test_deploy_doc.py` keeps in step with this list — rename an item here and
the test fails until the runbook follows):

- [ ] **ACME on the real domain.** The DNS A/AAAA records point at the server, and Caddy obtains
  the certificate: check the caddy logs and `curl -I https://<domain>/healthz`.
- [ ] **`.env` permissions.** `chmod 600 .env` and the file is owned by the deploy user; check
  with `stat -c '%a %U' .env`. NTFS on the rehearsal machine ignores modes.
- [ ] **Listening sockets.** `sudo ss -tlnp` shows nothing but 22/80/443 on public addresses. The
  rehearsal could only check `docker ps`.
- [ ] **GHCR login.** `docker login ghcr.io` on the server with a read-only token
  (`read:packages`), then `docker compose pull`. The rehearsal pulled with the workstation's
  login.
- [ ] **MLflow over an SSH tunnel.** Start the socat forwarder on `127.0.0.1:5001`, run
  `ssh -L 5001:127.0.0.1:5001`, then open the UI. Only the forwarder itself was checked locally.
- [ ] **Web Push to Mozilla through the SSRF guard.** Subscribe from Firefox, follow a live
  match, and confirm a push arrives (or the worker logs a 2xx). FCM and Apple answered through
  the pinned address from the dev network; `updates.push.services.mozilla.com` was unreachable
  from there even with plain curl.
- [ ] **Automatic rollback, provoked once.** On the live stack, deploy a release that is known to
  fail its checks (or make `/api/ready` fail on purpose) and confirm `deploy.sh` restores the
  previous release by its stored digests, then that the site and `/api/ready` are back. Drilled
  on the local stack in the rc5 rehearsal (exit 2 and exit 3); not yet on a server.
- [ ] **Real client addresses for IPv4 and IPv6 (F5, launch blocker).** Run
  `scripts/diagnose-client-ip.sh` on the server and, while it watches, open a match page from an
  IPv4 and from an IPv6 client, **signed out** (a guest: a signed-in caller is counted by user id,
  not address). It must print "IPv4 real client seen: yes", "IPv6 real client seen: yes" and
  "Gateway / internal address seen: no", and list a new guest quota key under each real address
  (IPv6 as its /64). The admin system health page must show `client_ip` ok. Do not open the site
  until all pass; on a failure, read part 1 of its output (IPv6 on the network, NAT rules,
  docker-proxy) and see §9m F5.
- [ ] **Refresh replay subnet binding (F13 B), after the point above passes.** Sign in from a
  client in one subnet, make it lose a rotation answer (e.g. copy the refresh cookie before a
  page load, then present the old cookie again within 60 s with the same user-agent), and check
  that the same client is served the replay (`auth.token.refresh_replayed`). Then present the
  same old cookie from a second client in **another** subnet (other IPv4 /24, other IPv6 /64)
  with the same user-agent: it must be refused (`meta.replay = "subnet_mismatch"` on the
  `auth.token.refresh_conflict` or `auth.token.reuse_detected` row). Do it for IPv4 and IPv6.
- [ ] **Rollback to a previous release by digest.** As soon as a second release with a digests
  file exists (rc5 or v0.0.1 after rc4), deploy it, then `IMAGE_TAG=<previous> scripts/rollback.sh`
  and check every app container runs the previous release's digests. Done locally in the rc5
  rehearsal (rc5 → rc4 by digest, then rc5 again); not yet on a server.

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
  Until 2026-10-08 it re-prompted on every page load (F12, §9m): the cookie name now lives in
  `lib/ageGate.ts`, not in the client component.
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
  env, same Redis/Postgres/MLflow) and stopping it on the main host.

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

## 9l. Data providers — owner decisions (2026-10-03)

The production data stack is decided. This section records it. The research, cited doc pages and
the open questions are in `docs/DATA_SOURCES.md` §3–§5.

**Decisions**
- **football-data.co.uk is never used in production.** It has no commercial licence and stays
  dev/local only (`licensed_for_production = False`, already enforced).
  - Any football-data rows that reach production are **replaced by Sportmonks data after the
    backfill**; they must not stay. Check first with `data-report` (fixtures per source).
- **Primary providers:**
  - **Sportmonks (Growth plan):** fixtures, results, HT/FT, stats, live.
  - **The Odds API:** odds — historical snapshots for the backfill, plus forward capture that
    builds our own odds history.
- **Fallback candidates:** API-Football and TheStatsAPI.
  - **API-Football is already integrated for live:** the in-play poll is implemented and runs in
    dev. Production has never been launched. Only an API-Football fixtures/results *fallback
    adapter* is unimplemented.
  - **TheStatsAPI is not implemented** at all.
  The `SourceAdapter` interface must keep room for them.
- **History depth:** seasons 2019-20..2025-26 for EPL, LaLiga, Serie A, Bundesliga, Ligue 1. The
  Russian Premier League comes in a separate, later PR. The Odds API lists
  `soccer_russia_premier_league`, but its bookmaker coverage and Sportmonks Growth RPL coverage are
  unverified.
- **Reference odds by kickoff date** (`REFERENCE_BOOKMAKERS`): Pinnacle before 2025-07-23,
  `market_avg` from that date.
  - **Decision rule for the trial month.** If The Odds API Pinnacle closing snapshots after
    2025-07-23 cover **≈ 90 %** of matches, with plausible timestamps and prices consistent with
    other bookmakers: keep Pinnacle as the primary reference and `market_avg` as the fallback.
    Otherwise: `market_avg` is primary after 2025-07-23, and Pinnacle is used only for older seasons.
  - Soft bookmakers (e.g. Bet365) are **never** the primary reference.
- **Odds backfill scope:** **h2h (1X2) only** first (≈ 70k credits, one month of the 100K plan).
  Totals (O/U 2.5) come later, after coverage is checked: The Odds API notes that spreads/totals
  are "mainly available for US sports and bookmakers", so totals in the `eu` region may be sparse.
- **Store every `eu` bookmaker** from each paid snapshot, not only Pinnacle. The snapshot is
  already paid for, and the stored quotes let `market_avg` be recomputed.
- **`market_avg` is computed by us** (The Odds API has no market-average price):
  - inputs: `eu` region, no exchanges, at least 5 bookmakers per outcome;
  - an outlier guard drops quotes far from the median;
  - the bookmaker count is stored with the record;
  - the exact definition is in `docs/DATA_SOURCES.md`. It is not football-data's `AvgC*`.
- **No Sportmonks xG add-on for now.**
  - Sportmonks xG exists only from the 2024 season.
  - Mixing a provider xG (2024+) with shot-based approximate xG (older seasons) in one feature is a
    **distribution-shift risk**.
  - Revisit at the "xG quality" feature-group step, with **one** xG definition across the whole
    history.
- **Closing odds start on 2020-06-06** (The Odds API historical data).
  - The ≈ 1,450 matches of 2019-20 played before the restart have **no closing odds** in
    production. They remain useful for results and Elo/Glicko warm-up.
  - Market comparisons (ROI vs closing, market model, backtester) start in 2020-21.
  - The 2019-20 post-restart tail has odds: about 360 matches, none in Ligue 1, whose season was
    cancelled.

**Licences.** No adapter flips `licensed_for_production` to `True` without the owner's written
confirmation.

| Provider | ToS position | Flag |
|---|---|---|
| **The Odds API** | Explicitly allows commercial use, indefinite storage, ML training and derived data | may be `True` |
| **Sportmonks** | Allows commercial use and storage; forbids direct resale; **silent on ML training** | `False` until written confirmation |
| **API-Football** | Betting-related use may need additional licences from rights holders; silent on storage/ML | live poll gated by an explicit flag, kept **enabled** for now (see below) |
| **TheStatsAPI** | Forbids caching/storing beyond what is reasonably necessary; the right to use data ends on termination | unusable for training/history unless confirmed in writing |

- **API-Football live poll.** It is implemented and runs outside `ensure_licensed`: so far in dev
  only, since production has never been launched. It gets the same kind of licence flag, set to
  enabled until the owner confirms.
  - This is an explicit owner decision: the poll stays on while the written answer is pending.
  - The question to API-Football must also cover **publishing live data and derived predictions**
    on a betting-analytics product.
  - Moving live to Sportmonks is a later item.
- **Owner action — written answers from every provider** on:
  1. indefinite storage of historical data;
  2. ML training;
  3. showing derived metrics to users;
  4. betting-analytics use, including publishing live data and derived predictions;
  5. what happens to stored data after the subscription ends.

**Provider facts that shape the design** (cited in `docs/DATA_SOURCES.md`)
- **Sportmonks** odds history lasts only ≈ 7 days after kickoff, so it cannot backfill odds.
- **The Odds API** historical snapshots:
  - start 2020-06-06; 10-minute interval, 5-minute from 2022-09-18;
  - the snapshot returned is the closest one **at or before** `date`;
  - cost = `10 × markets × regions` per request.
  - Pinnacle is in `eu` only, with the docs note "odds are from public website which may incur a
    delay". Its reliability after 2025-07 is **unverified**.
- **Ids are provider-specific.** Matching uses `fixture_external_refs`, `provider_team_aliases`
  and the ±36 h identity rule (HI-2). The Odds API names teams by string, so its aliases are
  bootstrapped through `map-team` / `unmapped-teams`, never auto-accepted.

**Secrets**
- **Keys.** Keys are entered in **Admin → Providers**: encrypted in the DB, rotatable without a
  redeploy. `.env` is the fallback. CI secrets are used only by the contract-check workflow.
  - **Current state:** the admin key is stored but **not yet read** — the live poller still uses
    `API_FOOTBALL_KEY` from `.env`. Provider PR 1 adds the lookup: DB key first, `.env` fallback.
- **What a database dump exposes.**
  - A `pg_dump` contains provider and LLM keys (and TOTP secrets) **only as Fernet ciphertext**,
    plus a 4-character `key_suffix` used for masking.
  - The decryption key is `DATA_ENCRYPTION_KEY`, which lives in the server `.env` and never in the
    database. Back it up **separately** from the dumps: one leaked file alone is not enough, and
    losing the key makes every stored secret unrecoverable.
  - `SECRET_KEY` (JWT signing) also lives only in `.env`.
- **Keys never appear in chat, logs or audit.**
  - The Odds API takes the key only as an `apiKey` query parameter. Sportmonks takes the token in
    the `Authorization` header or as `?api_token=`.
  - **Outbound scrubber (shipped 2026-10-05, `app/core/outbound.py`).**
    - **Clients.** Every outbound HTTP call goes through `outbound_client(secrets=[...])` and
      `check_status()` (never `raise_for_status`). Their exceptions are copies of the same httpx
      class, rebuilt on the redacted URL, with no exception chain, so the original request and
      its headers cannot be reached. Today that covers Telegram (push and ops alerts),
      API-Football, football-data and Web Push.
    - **Guard.** `tests/test_outbound_guard.py` fails on any other client in `app/`: httpx async or
      sync, `raise_for_status`, requests, aiohttp, urllib3, urllib.request, http.client.
      - The one exception is the OpenAI SDK in `app/services/llm/analysis.py`. Its key is a
        header, it is registered as a secret, and a test checks that an SDK error carries no key.
      - **New provider clients (Sportmonks, The Odds API) must use `outbound_client` and pass
        their key in `secrets`.**
    - **Log safety net.** `install_log_safety()` runs at every entry point: `create_app`, the
      ARQ worker module, `app.cli`, `app.bootstrap`. It wraps the LogRecord factory, so every
      logger's message, arguments, traceback and stack are redacted.
    - **What is redacted:**
      - URL userinfo;
      - query parameters `apikey`, `api_key`, `api_token`, `key`, `token`, `access_token`,
        `refresh_token`, `auth`, `secret`, `client_secret`, `password`, `signature`, `sig`, also
        in a bare path;
      - Telegram bot tokens;
      - the values of sensitive headers (`authorization`, `x-apisports-key`, `x-rapidapi-key`,
        `x-api-key`, `api-key`, `x-auth-token`, cookies);
      - every registered secret, raw and URL-encoded: settings secrets at start-up, and client
        keys when they are used.
    - **Quiet loggers.** `httpx`, `httpcore` and `openai` stay at WARNING whatever the app's log
      level: their INFO/DEBUG lines carry full URLs or request options.
    - **Fixtures guard.** The same test file scans `tests/fixtures/**` (saved provider responses,
      now and later) for key-looking strings and for the value of any secret in the environment
      or the local repo-root `.env`. Failures name the file and the kind of match, never the
      value. Replace keys with `REDACTED` before committing a fixture.
    - **If Sentry is wired up** (`SENTRY_DSN` is read nowhere today), add a `before_send` that
      runs `redact_text` over the event, and do not send local variables.
  - Provider audit entries record field names only (already the case).

- **Replacing football-data rows in production (provider PR 2b) — future upgrade only.**
  Production has never run, so on the first launch there are no football-data rows to replace,
  and PR 2b is needed only if they ever reach a production database. A plain Sportmonks backfill is
  not enough. It links its record to an existing football-data fixture, but under the HI-3 rules a
  different source never overwrites a stored score (it only records a conflict). The `odds` table
  also has no source column, so football-data quotes could not be told apart from The Odds API
  quotes later. PR 2b is therefore a dedicated operation, `replace-source --from football_data_couk
  --to sportmonks`, that runs **after the Sportmonks backfill and before the Odds API backfill**:
  - **Dry run (default).** Lists every football-data fixture with its Sportmonks counterpart
    (by `fixture_external_refs` / the ±36 h rule), and every fixture without one.
  - **Global preflight.** The real run first repeats the full match check across **all** selected
    leagues/seasons, before its first write. Any fixture without a counterpart (or with two) STOPs
    the run with the list, and nothing is written; nothing is guessed.
  - **Before the real run:** a manual `pg_dump` (pre-deploy checklist, §9i). This is the only path
    for a full rollback.
  - **Real run.** One transaction per league/season. For each matched fixture it:
    - overwrites kickoff, `kickoff_time_known`, scores, status and stats with the Sportmonks
      values;
    - sets `fixtures.source = 'sportmonks'`;
    - deletes the fixture's football-data odds and its `fd:` external refs;
    - audits every change.
  - **Failure mid-run and resume.**
    - A failed league/season rolls back only itself. The seasons already committed stay replaced,
      so a mixed state is possible between runs. It is visible as per-source counts in
      `data-report`.
    - The operation is **idempotent and resumable**: a fixture already replaced
      (`source = 'sportmonks'`, no `fd:` ref) is skipped, so re-running the same command after the
      cause is fixed completes the remaining seasons. Each run prints which seasons were done,
      skipped and failed, and exits non-zero on any failure.
    - The Odds API backfill **refuses to start** while any `football_data_couk` fixture remains.
      A mixed state therefore cannot leak into odds.
    - A full rollback means restoring the pre-run dump.
  - **Afterward.**
    - `data-report` must show zero `football_data_couk` fixtures.
    - The operation itself queries `odds` for **each replaced fixture id** and fails unless none
      has a row left (coverage per league/season is not enough).
    - The Odds API backfill repeats that per-fixture check before it starts.

**Trial and PR order**
1. **Free calls first**, on the free The Odds API key: `/sports`, `/events` and two small `/odds`
   calls (≈ 3 credits).
2. **Historical calls** (≈ 51 credits) only after the owner buys the $30 plan.
3. **Sportmonks** calls during its 14-day trial, which the owner starts when ready.

Sanitised real responses (no keys, no account data) become test fixtures; a contract test fails
loudly on a shape change.

PR sequence (reordered by the owner on 2026-10-05: PR 1 moved first because the Sportmonks trial
ends about **2026-10-17**, and its saved responses must be recorded before then):

| Step | PR |
|---|---|
| 0 | Data-provider docs PR — **done** |
| 1 | **Urgent — first.** HTTP foundation through `outbound_client` (keys registered with the scrubber, key lookup, retries, quotas, credits logged per call) + real responses saved as fixtures (fixture guard scans them), contract tests. Order of calls: free The Odds API calls, then the cheap paid ones, then the Sportmonks list |
| 0b | `data-report` fixtures per source |
| — | Live base rates from current Dixon-Coles estimates (variant a) |
| 2 | Sportmonks adapter + resumable backfill |
| 2b | `replace-source` for football-data rows in production, if any — see above |
| 3 | Aliases + data-report mapping quality |
| 4 | The Odds API closing backfill: dry-run cost, budget guard, snapshot ledger, `market_avg` |
| 5 | Forward odds capture |
| 6 | Dev-only Pinnacle check against football-data (nothing stored) |
| later | RPL; live on Sportmonks |

**Provider PR 1 — status (2026-10-05).** Shipped on `feat/provider-http-foundation`:
- `app/providers/http.py`: a minimal GET client on `outbound_client` (key registered with the
  scrubber), bounded retries (429 with `Retry-After` / Sportmonks' `rate_limit` reset, 5xx,
  transport errors), one log line per call with the quota (The Odds API credits, Sportmonks'
  per-entity `rate_limit`). `TheOddsApiClient`, `SportmonksClient`; no adapters yet.
- `python -m app.cli provider-record --provider P --manifest FILE [--env-file ../.env]
  [--execute] [--max-credits N]`: replays a committed manifest; dry run by default; credit cap and
  expected statuses checked per call; keys only from the env file; fixtures keep only the provider
  key replaced with `REDACTED` and drop account blocks.
- Real responses under `tests/fixtures/{the_odds_api/free,sportmonks/trial}/`, pinned by
  `tests/providers/test_recorded_contracts.py`. The fixture guard has two owner-approved,
  file-scoped exceptions for The Odds API `"key"` fields (exact values in `odds_epl_h2h_eu.json`;
  snake_case ids in `sports.json` only).
- Findings and verdicts: [`docs/provider-evaluation-2026-10-05.md`](./docs/provider-evaluation-2026-10-05.md).
  The Odds API spent 3 credits of the demo key's 500.

**Deferred from PR 1 (owner decision: keep the HTTP base minimal):**
- Key lookup: the key from Admin → Providers (encrypted `provider_accounts`) first, `.env` as the
  fallback, registered with the scrubber. Today `provider-record` reads keys only from the env file.
- Moving the API-Football live poller to that lookup (it still reads `API_FOOTBALL_KEY`): replace
  `live_provider_configured()` and `build_live_provider()` in `app/services/live/provider.py`.
- Storing the latest quota in `provider_accounts.quota_state` (with the PR 4 budget guard).

**Waiting on the owner (do not act without an ok):**
- **Sportmonks live format retries** — each one needs the owner's ok (the owner pings in time),
  then `/livescores` and `/livescores/inplay` once:
  - RPL, Friday 2026-10-09, from about 16:45 UTC;
  - EPL, Saturday 2026-10-10, from about 11:45 UTC.
- **Sportmonks written answers (received 2026-10-05):**
  - Growth gives the **latest 3 seasons** (matches the trial: 2024/25–2026/27).
  - Older seasons come with the **Historical Data add-on: one-time 99 EUR excl. VAT, no trial,
    paid upfront**. Provider PR 2 (adapter and backfill) is designed around this: 2019-20..2023-24
    only after the add-on is bought.
  - **Licence:** our own commercial model training and publishing derived probabilities are
    allowed; reselling or redistributing raw data is forbidden. `licensed_for_production` may
    become `True` for Sportmonks on that basis once the owner confirms in writing in the repo.
  - **Payment:** card, PayPal, and bank transfer (bank transfer only for annual plans).

**Known dev-only side effect.** The default `DATABASE_URL` password is `football`, and the log
scrubber registers settings secrets of 8+ characters, so in dev every log line shows `football` as
`***` (e.g. `/v3/***/leagues`). Production passwords are long and random, so it does not occur
there. Left as is (owner decision); `provider-record` does not use that registry for fixtures.

**Trial-month checklist (owner)**
- [ ] Pinnacle closing data quality after 2025-07-23, measured against the decision rule above.
- [ ] League coverage at both providers, including RPL.
- [ ] Credit consumption against the estimate.
- [ ] Sportmonks: RPL coverage, bookmaker list, odds availability.
- [ ] Payment and account access for a customer in Russia.
- [ ] Written answers from every provider (the five points above).

## 9m. External review (2026-10-05) — verified backlog

Another agent reviewed the code by static analysis; it ran no tests. Each finding below was then
**verified by code reading against main `5eedb0b`, not reproduced by tests**: every fix still
starts with the failing test named in its row. The reviewer's confidence percentages were
ignored. Line numbers are as of `5eedb0b` and will drift.

**Labels.** Findings carry the `ER-` prefix. The bare labels in §11 (`M-02` backtester memory,
`H-02`/`L-01` LLM) belong to an older audit and are different items.

**Mandatory notes** (part of each fix's definition of done):
- **ER-C-01 — acceptance criterion.** Upcoming matches are not listed at all today: `/matches`
  keeps only fixtures with `exists(Prediction)`. The pre-match inference task must also make
  scheduled fixtures visible. Done means a scheduled fixture appears in `/matches` with a
  consensus.
  - **Design requirements for the pre-match inference task** (owner, 2026-10-07, from a third
    external note — a product wishlist, not defects):
    - **Every published pre-match prediction is an immutable record:** fixture, method,
      `model_version`, feature-set version, `prediction_time`, the probabilities, and the market
      odds snapshot used at that moment (bookmaker or `market_avg`, with the snapshot's own
      timestamp; `app/ml/odds_selection.py` already picks the latest complete snapshot at or
      before a given time). Never updated in place: a re-run writes a **new** record. Today's
      `predictions` table cannot hold this as is — it is unique per `(fixture_id, method, market,
      outcome, model_version)` and training inserts with `ON CONFLICT DO NOTHING`
      (`ml/training.py`), so a re-run under the same model version writes nothing, and it has no
      `prediction_time` (only `created_at`), feature-set version or odds snapshot.
    - **After settlement** the closing odds and the result are stored **outside** the immutable
      prediction records (a separate settlement record per fixture, or per prediction, that
      references them) and associated with them, never written into the prediction rows; CLV and
      a true forward test (predictions frozen before kickoff, scored after) are computed from the
      pair. This is the §11 ML follow-up "a real forward test".
    - **A feature-set version identifier** is introduced with ER-C-01, so predictions made under
      different feature definitions are never compared blindly. Today there is none: each MLflow
      run logs `feature_schema.json` (`ml/mlflow_utils.py`, from `ml/features.feature_schema()`),
      a column list, not a version; a hash of it is one way to derive the identifier.
    - Already in place and **not new work** (checked in the code): the model registry with
      snapshots, one-champion index and registry lock (`ml/registry.py`, `models/model_registry.py`,
      §6 "Model governance"); the champion rule (`apply_champion_selection`); isotonic calibration
      of the consensus (`ml/consensus.py`); tier feature flags (`services/tiers.py`, §9b).
      Provider reconciliation exists **only in part**: the ±36 h cross-source identity rule,
      `ingestion_conflicts` and `data-report` (HI-2/HI-3, §6); reconciling Sportmonks and The Odds
      API is provider PRs 2–4 (§9l).
  - **Rejected from the same note, with the reason:**
    - a user bet ledger and a "recommendation" lifecycle — the §9j product rule: no calls to
      place bets without legal review;
    - business analytics — deferred until payments exist and the cookie-consent banner (§11) is
      built (analytics only after consent).
- **ER-H-02 — spend under-reporting.** The analysis upsert overwrites the `llm_analysis` row, and
  the LLM spend dashboard sums those rows, so every regeneration (language switch, cache expiry)
  drops the earlier generation's tokens and cost. The fix must correct the spend figures as well
  as the cache key.
  - **Fixed 2026-10-07 (PR B):**
    - migration **0018**: unique `(fixture_id, model, language)` on the analysis cache, which
      is looked up by language;
    - an append-only **`llm_generations`** journal: one row per LLM call (a cache hit writes
      none), in the same transaction as the cache upsert, tokens and **cost computed and stored
      at write time** (a later price change never rewrites history);
    - the spend dashboard aggregates from the journal, not from the cache rows;
    - **history before 0018 is lost**: the generations the old upsert overwrote cannot be
      recovered, so the journal starts with one back-filled row per cached analysis (its last
      generation);
    - **downgrade** keeps the newest row per `(fixture_id, model)` (`created_at DESC, id DESC`),
      deletes the rest, and raises a `NOTICE` with the number of deleted `llm_analyses` rows and
      of `llm_generations` rows it drops (shown in the alembic log, see §6); dump first (§9i,
      pre-deploy checklist, exact command there);
    - **rollback incompatibility (ER-H-08):** an image before 0018 fails LLM generation on the
      0018 schema (§9i); disable LLM or downgrade first. Not verified in the rc6 rehearsal (no LLM
      provider configured); still open.
- **ER-H-09 — required tag** (fixed with F2, see §9i "Release images and digests"). Before the
  fix, `infra/docker-compose.prod.yml` fell back to `${IMAGE_TAG:-latest}` (lines 21, 46, 89):
  `deploy.sh` refused `latest`, a manual `docker compose up` did not. The overlay now requires
  `${IMAGE_TAG:?}` and a digest for every app image (no fallback).

| ID | Verdict | Evidence | Severity | Smallest fix | First failing test | Slot |
|---|---|---|---|---|---|---|
| ER-C-01 | Confirmed; the failure differs | `Prediction` is written only by training (`ml/training.py:300`); `/matches` requires `exists(Prediction)` (`api/matches.py:231-232`), so upcoming matches are not listed at all (not listed with `consensus: null`). Same gap as §11 "online inference" | Critical for launch | Scoring task: load the champion, build as-of features, write `Prediction` for scheduled fixtures | A scheduled fixture, after the task runs, is in `/matches` with a consensus | Right after real data is loaded |
| ER-H-01 | Confirmed, wider | Every poll tick enqueues a recompute for **every** live fixture (`services/live/ingestion.py:118`, `workers/tasks.py:108-117`) with no `_job_id`, `max_jobs=20`: duplicates and out-of-order states (an older minute stored after a newer one) | Medium now (live is dev-only); High when live is public | Per-fixture `pg_advisory_xact_lock` in `recompute_fixture`; skip a state not newer than the latest | Two concurrent `recompute_fixture` calls with the same new state → one `LiveUpdate`, one `should_push` | With live moving to Sportmonks, together with ER-M-02 |
| ER-H-02 | Confirmed, plus spend | Cache lookup and `uq_llm_analysis_fixture_model` ignore `language` (`services/llm/analysis.py:170-174`, `models/llm.py:62`); upsert overwrites (`analysis.py:218-229`); spend sums the rows (`services/llm/spend.py:81-83`) | High | Migration: unique `(fixture_id, model, language)`, filter by language; keep every generation for spend (see note) | `ru` then `en` → two LLM calls, `en` content served; spend counts both | **Fixed 2026-10-07** (migration `0018`, `llm_generations`; note above) |
| ER-H-03 | Confirmed | Budget check and `INCRBY` are separate (`analysis.py:193-214`); `INCRBY` + `EXPIREAT` not atomic (also in §11); concurrent cache misses all call the LLM | High (cost) | Atomic Lua reservation of `max_tokens` in `app/services/counters.py` style, settled with the real usage after the call | 20 concurrent requests with budget left for one → at most one `generate_completion` call | LLM pack, before user-facing launch |
| ER-H-04 | Confirmed | `AsyncOpenAI(...)` never closed (`analysis.py:130-131`) | Low–Medium | `async with AsyncOpenAI(...)` | A fake client records `close()` | LLM pack |
| ER-H-05 | Confirmed | `session.get(Fixture)` (`api/llm.py:98`) checks out a request-pool connection that is held through the LLM call until `commit` (line 110); request pool = 15 | High under load | Read, close the session, call the LLM, write in a short separate session | `pool.checkedout() == 0` while a stubbed `generate_completion` runs | LLM pack |
| ER-H-06 | Confirmed, wider | FastAPI 0.139 closes yield dependencies after the response; `require_streaming_tier` and `get_current_user` use `get_db` (`api/live.py:56-64`, `core/deps.py:49-51`), so **every open stream holds a write connection with an open transaction**, replay or not. Not reachable today: Caddy sends only the Telegram webhook to the API (`infra/Caddyfile:36-41`) and the BFF has no stream route | High before SSE is exposed | Resolve user/tier and replay in short sessions; the stream itself uses Redis only | `checkedout() == 0` while a stream is open | Before SSE is exposed |
| ER-H-07 | Partly true | Unbounded fetch (`services/backtester/engine.py:186-197`), but `backtest_features` has one row per fixture: 5 leagues × 7 seasons ≈ 13k rows, not 1M; runs are tier-limited per day | Low–Medium | Row cap and a narrow select | A run over the cap → explicit error/refusal | Later |
| ER-H-08 | Confirmed (policy) | Rollback restores images, not schema (`scripts/deploy.sh:123-131,141`); `scripts/rollback.sh:97` does so on purpose | Medium–High at the first upgrade with migrations | Expand/contract migration policy + PR checklist | CI job: release N−1's tests against head schema (design separately) | Before the first public release. **First real case: `0018`** — a pre-0018 image fails LLM generation on its schema; disable LLM or downgrade first (§9i) |
| ER-H-09 | Confirmed, plus fallback | Tags are not pinned to digests (`.github/workflows/release.yml:74-76,89-91`); `${IMAGE_TAG:-latest}` in prod compose (see note) | Medium | With F2: record digests in the release, deploy by digest, `${IMAGE_TAG:?}` | Release-tooling test: `compose config` without `IMAGE_TAG` fails | With F2 |
| ER-H-10 | Partly true | Order is `(kickoff_at, id)` only (`ml/chronology.py:30-38`); date-only fixtures sit at 12:00 UTC. Production never uses football-data, and Sportmonks has kickoff times, so this hits dev data and old CSV seasons | Low for production; Medium for dev-metric honesty | Conservative chronology: a league-day with any unknown-time fixture is one batch | An unknown-time match's result never changes a same-day known-time match's prediction | Later |
| ER-M-01 | Confirmed | `/health/ready` returns `ready` unconditionally (`api/health.py:42-49`); also §9g 12d item 1 | Medium | `SELECT 1` + Redis `PING` with timeouts, 503 on failure | Redis ping fails → 503 | Before the first public release. Prior work: PR #26 (closed unmerged, last commit 2026-07-19: extended system health and readiness, 24 files, out of date; reference only; its branch `codex-wybhg7` was deleted 2026-10-09, the PR keeps the commits) |
| ER-M-02 | Confirmed | If the enqueue in `finally` fails (`workers/tasks.py:120-127`), the self-rescheduling chain ends until a worker restart (seeded only at start-up, `workers/arq_app.py:80-84`) | Medium | A cron re-seeds the poll with a fixed `_job_id` | Enqueue in `finally` raises → the cron restores the chain | With ER-H-01 |
| ER-M-03 | Confirmed | `BatchCreate.size` has no upper bound (`schemas/promo.py`), loop in `services/promo.py:144`; admin + TOTP only | Low | `le=100_000` | `size=100_500` → 422 | **Fixed 2026-10-06** (schema `le=100_000` + `BATCH_MAX` in the service) |
| ER-M-04 | Confirmed | `/auth/register` has no rate limit and answers 409 for a known address; not routed today (the BFF has only login/logout/me/refresh) | Medium before sign-up opens | §11 O2; the limit runs **before `hash_password`** (ER2-04) | §11 O2 | O2 |
| ER-M-05 | Confirmed | The match-view quota is consumed before the fixture lookup (`api/matches.py:319-343`); a guest burns mostly their own (IP or /64) quota, NAT neighbours share it | Low–Medium | Consume after the lookup | Random UUID → 404, remaining quota unchanged | **Fixed 2026-10-06**; the 404 before the quota check is deliberate (an exhausted caller can tell real ids from made-up ones; matches are public in `/matches`) |
| ER-M-06 | Partly true | `ilike('%x%')` without an index (`api/system.py:87-100`); user `%`/`_` are not escaped; admin-only, small log at launch | Low | Escape wildcards; `pg_trgm` later | A literal `%` in `target` matches only itself | Later |
| ER-L-01 | Confirmed, negligible | `XgModel` built in the loop (`ml/features.py:190-194`), but its `__init__` is trivial (`ml/xg.py:35-36`) | Low (cosmetic) | Hoist out of the loop | — | Later |
| ER-L-02 | Confirmed | O(n·k) season grouping (`services/backtester/engine.py:241-252`), k ≤ 7 | Low | `defaultdict` | — | Later |
| ER-L-03 | Confirmed | Stale module docstring (`api/matches.py:1-7`) | Low | Docstring rewrite | — | **Fixed in this docs PR** |

ER-M-05 follow-up (CodeRabbit, same PR): with a 404 no longer spending the daily quota, unknown
ids had no limit at all, so `GET /matches/{id}` now has a per-caller request limit **before** the
fixture lookup: `rl:match_detail:{identity}` (the quota's identity), `RATE_LIMIT_MATCH_DETAIL_PER_WINDOW`
= 120 per `RATE_LIMIT_MATCH_DETAIL_WINDOW_SECONDS` = 60, 429 with `Retry-After`. A Redis error
fails the request (fail-closed), as on `/analysis`.

### Found later (2026-10-06/07): F5–F9

Found while checking the ER-M-05 follow-up; the F-series continues the rehearsal findings (F1–F4,
§9i). F6, F8 (2026-10-07) and F9 (2026-10-08, found while planning F8) are fixed; F5 is fixed in code (2026-10-09, server
verification pending) and F7 is fixed (2026-10-09).
F10–F18 were found in the rc6 rehearsal and after it (below); F10, F11, F12 and F14 are fixed.

- **F5 — all IPv6 guests may share one identity. LAUNCH BLOCKER for the first VPS run.** Caddy
  publishes `[::]:80`/`[::]:443`, but the `betpulse` network is IPv4-only, so Docker hands IPv6
  connections to its userland proxy (`docker-proxy`), and Caddy then sees them coming from the
  bridge gateway, `172.29.89.1`. Every IPv6 **guest** would be one identity (signed-in callers
  are counted by user id and are not affected). The application chain
  itself is correct (Caddy drops incoming `X-Forwarded-For`, the BFF forwards the right-most
  address, the API trusts only `web`/`caddy`); in the rc5 rehearsal a request through Caddy and
  the BFF was counted under the real client address, which on Docker Desktop is the gateway for
  all host traffic, so the VPS behaviour is unverified. **Affects today** the daily guest quota
  (3 views/day shared by every IPv6 guest) and, **after the ER-M-05 follow-up**, the new
  match-detail rate limit (120/min shared by those guests).
  - Candidate fixes:
    1. **IPv6 on the compose network** (`enable_ipv6: true` with a ULA subnet, Docker ≥ 27 with
       `ip6tables`): Docker then NATs IPv6 like IPv4 and keeps the source address. Pin
       IPv6 addresses for `web` and `caddy` too and add them to `TRUSTED_PROXY_CIDRS`, or keep the
       internal hops on IPv4; otherwise the API would see the BFF's IPv6 address as the client.
    2. **Caddy with `network_mode: host`**: real addresses for both families, but Caddy leaves
       the pinned network (its `172.29.89.11` entry in `TRUSTED_PROXY_CIDRS`, its route to `web`
       and `api`) and binds the host directly.
    3. **No IPv6 at the edge**: publish IPv4 only and no AAAA record. Simplest, but IPv6-only
       clients cannot reach the site.
  - **Recommendation:** 1; if it cannot be finished before launch, 3 as a stopgap (never launch
    with the collapse in place).
  - **Fixed in code 2026-10-09 with option 1 (PR `fix/f5-ipv6-client-identity`). Verified on a
    server: NOT YET** — the first-VPS checklist item (§9i) with `scripts/diagnose-client-ip.sh`
    decides it; until it passes, the closed-trial rule (§9i) stays.
    - **Network** (`infra/docker-compose.yml`, base file, so dev, CI and prod match):
      `enable_ipv6: true`, subnets `172.29.89.0/24` and the ULA `fd42:b7e1:5a29:89::/64`, gateways
      pinned (`.1`, `::1`). `web` and `caddy` pinned in both families (`::10`, `::11`); variables
      `BETPULSE_NETWORK_SUBNET(6)`, `BETPULSE_NETWORK_GATEWAY(6)`, `BETPULSE_WEB_IP(6)`,
      `BETPULSE_CADDY_IP(6)` (`.env.example`). `TRUSTED_PROXY_CIDRS` = the four /32 and /128
      addresses; `INTERNAL_NETWORK_CIDRS` = the two subnets (api and every worker). Internal hops
      stay on IPv4 in practice (RFC 6724 ranks IPv4 above ULA), but an IPv6 hop is trusted too.
      Caddy: comment only (no `trusted_proxies`).
    - **Guard** (`app/services/client_identity.py`, middleware in `main.py`): a client identity a
      trusted proxy vouched for that lies inside `INTERNAL_NETWORK_CIDRS` (the gateway, or an
      internal hop the API does not trust) → one warning per API process, a Redis record
      (`ops:client_identity:internal`: count, first/last time, last address and path; flushed at
      most once a minute per process; 24 h TTL), and the admin system health component
      `client_ip` = degraded while it exists. Requests without `X-Forwarded-For` (deploy.sh's
      `/api/ready` from inside web) are not counted. **Known false positive:** a request the
      server sends to itself through Caddy (`curl https://localhost` on the host) also arrives as
      the gateway; the warning and the health detail say so. Production requires
      `INTERNAL_NETWORK_CIDRS` (private, holding every trusted proxy).
    - **Checks:** `scripts/check-compose-ports.sh` fails unless the network is dual-stack with one
      subnet per family, web and caddy are pinned in both inside them, and the api and every
      worker trust exactly those addresses and name exactly those subnets
      (`scripts/tests/check-compose-ports-test.sh`, six broken variants). The connection-budget
      test needed no change (it reads only environment, command and replicas).
    - **CI probe** `scripts/edge-identity-smoke.sh` (job "Release tooling"): the prod config with a
      stub `web` that echoes `X-Forwarded-For`; a client in its own network namespace (veth to
      the host, so its packets enter like an outside client's) calls the published port 80 over
      IPv4 and IPv6. **Red on main** (runner Docker 28.0.4, Ubuntu 24.04): IPv4
      `xff=10.253.53.10` (its own), IPv6 `xff=172.29.89.1` (the gateway). A first version with a
      client *container* on another bridge was red for IPv4 too — Docker relays container-to-host
      connections through docker-proxy — so it was no model of an outside client. **Docker
      Desktop cannot run it:** there even IPv4 arrives as the gateway (checked, 29.8.1).
    - **Changing the network on an existing stack: one-time `down`.** Compose cannot change a
      network in place. `deploy.sh` and `rollback.sh` (`scripts/compose-network.sh`) compare the
      existing project network with the rendered config (IPv6, subnets, gateways) **before
      pulling or starting anything** and, on a mismatch, refuse with exit 1 (never a failed `up`
      and an automatic rollback) and print the procedure: `scripts/prod-compose.sh down`
      (**never `-v`**), then the same deploy/rollback command again. The site is down between
      the two. **First deploy on an empty server:** no network yet → no refusal; Compose creates
      it. Scenario tests 17a–17f in `deploy-scripts-test.sh`.
    - **Rollback compatibility:** rollback.sh uses the checkout's compose files, so after the
      one-time `down` a rollback to a release before F5 (rc6) runs on the dual-stack network;
      those images ignore `INTERNAL_NETWORK_CIDRS` (`extra="ignore"`) and accept the IPv6
      `/128`s in `TRUSTED_PROXY_CIDRS`. Reverting the F5 commit itself needs the one-time `down`
      again (the guard prints it).
    - **Diagnosis on the server:** `scripts/diagnose-client-ip.sh [seconds]` (read-only): Docker
      version and `daemon.json`, the network (IPv6, subnets, proxy addresses), who listens on
      80/443 (docker-proxy or not), the NAT rules for 80/443 (sudo), host IPv6 (addresses,
      default route, `forwarding`, `accept_ra` of the uplink), A/AAAA of `PUBLIC_DOMAIN`; then it
      watches while the owner opens the site from an IPv4 and an IPv6 client, signed out, and
      prints every address Caddy saw (`netstat` in caddy) and every new guest quota key in Redis,
      each marked "real client" or "OUR NETWORK (collapse)". Tried read-only on the rehearsal
      stack: it reported the collapse (`::ffff:172.29.89.1`), as expected on Docker Desktop.
    - **Verified locally (2026-10-09, Docker Desktop 29.8.1):** the dev stack (base file, project
      `f5dev`, subnets `.91`) came up with `enable_ipv6`, every service healthy with an IPv6
      address, web and its BFF reaching the API (`/api/ready` 200 once `API_BASE_URL` is
      `http://api:8000`; the `.env.example` value `http://localhost:8000` is for running outside
      Docker), real IPv4/IPv6 client headers answered normally, and the guard fired once on a
      gateway identity. In dev it fires on any direct hit of web's published port from the host
      (Next.js sets `X-Forwarded-For` from the socket, which is the gateway there): expected, dev
      only. The `bp-test` containers and the Linux pytest runner are on their own network and
      were unaffected (full suite green). **Rehearsal stack:** with the F5 files copied into its
      clone, `deploy.sh` and `rollback.sh` both refused (exit 1, procedure printed, containers
      untouched); `prod-compose.sh down`, then the same `deploy.sh` of rc6: exit 0, 9/9 healthy,
      the network dual-stack with web/caddy on `::10`/`::11`, the database summary identical to
      before, the site, `/api/ready` and a match page 200 through Caddy, `check-compose-ports.sh`
      OK. **The rehearsal clone now holds the F5 versions of `infra/` and `scripts/` as
      uncommitted changes**; after this PR merges, `git checkout -- infra scripts && git pull`
      there (the files will then match).
    - **Server requirements:** Docker Engine ≥ 27 (ip6tables on by default; nothing to set).
      Recommended, not required: `"userland-proxy": false` in `/etc/docker/daemon.json` — with it
      an IPv6 client of an IPv4-only network is refused instead of collapsing (loud, not silent),
      and Docker publishes through NAT only. Consequences and what still works: §9i
      "`docs/DEPLOY_VPS.md` outline". With forwarding on (Docker turns it on for IPv6), a host
      that takes its IPv6 route from router advertisements needs `accept_ra=2` on the uplink, or
      the route lapses; the diagnosis prints both.
    - **Backlog (not done here):** the guard catches only identities inside our own network. A
      heuristic for a collapse it cannot see — many distinct sessions or user agents behind one
      client address in a short time (a CDN in front, a carrier-grade NAT, a misrouted proxy) —
      could flag an address for review on the health page. Needs thresholds that do not flag
      ordinary carrier NAT; slot: with O2 or after launch.
  - First-VPS checklist item (§9i).
- **F6 — the daily view quota is spent by the page's own refetch. Fixed 2026-10-07 with option
  (a): the quota counts distinct `(identity, fixture)` per UTC day, for every tier (§9b).** Before:
  `useMatch` refetches `GET /matches/{id}` every 60 s (`frontend/lib/queries.ts`, plus
  `retry: 1`), and every call counts a view: a guest with one match tab open spends the 3 daily
  views in about 3 minutes without a click (a free user their tier's budget likewise).
  - Options: (a) count **unique `(identity, fixture)` per day** (a Redis set per identity and
    day: a fixture already viewed today is free; the quota limits distinct matches); (b) **no
    polling for guests**.
  - **Recommendation: (a).** It fixes every tier, not only guests, keeps live updates on the page,
    and matches what a "match view" means to a user; (b) leaves signed-in users burning their
    budget and drops live refresh for guests. Check the spec's wording of the quota with it.
    The spec says "Matches/day" and `tiers.py` already defined the limit as "distinct
    match-detail views per UTC day", so (a) brings the code in line with both.
  - Frontend follow-up: **F8** below.
- **F8 — the match page dropped its card on a failed background refetch; 4xx were retried.
  Fixed 2026-10-07.** TanStack Query keeps `data` when a refetch fails but sets `isError`, and
  `MatchDetailView` checked `isError` first, so one failed 60 s refetch replaced a card the user
  was reading with the lock (403) or the error text; the global `retry: 1` repeated 4xx answers,
  each counted against the 120/60 s request limit.
  - **Now:** the error or lock replaces the page only on a **first** load (no data). On a failed
    refetch the card stays: a 403 (the daily quota — spent, or lowered by a tier change; a tab
    left open past UTC midnight spends a view of the new day) shows the first-load wording
    ("Daily match limit reached" / «Дневной лимит матчей исчерпан», `limitReachedBody`) above it
    with "Below is the last data received." / «Ниже — последние полученные данные.»; anything
    else (404, 429, 5xx, network) shows "Couldn't refresh — showing data as of HH:MM." /
    «Не удалось обновить данные — показаны данные на HH:MM.». Both are `role="status"`.
  - **Polling** (`matchRefetchInterval`, `lib/queries.ts`): 404 stops; 403 waits for the next UTC
    midnight plus a stable 0–60 s offset per tab; otherwise 60 s. `useMatch` refetches on window
    focus (off app-wide). Recovery without a reload: a promo code (`RedeemPromo` invalidates the
    match queries), an upgrade (focus or reconnect refetch), the next UTC day (the midnight timer).
  - **Retries** (`lib/retry.ts`, set app-wide in `app/providers.tsx`): no retry on any 4xx, 401
    included (nothing relied on it: public match routes never answer 401, see F9); one retry on 5xx
    and network errors.
  - **`Retry-After`** from a backend 429 now reaches the browser (`proxyBackendGet` relays it and no
    other backend header). **No client backoff**, deliberately: the match-detail window
    (`RATE_LIMIT_MATCH_DETAIL_WINDOW_SECONDS` = 60) equals the poll interval, so the next poll
    already lands after the window. **Revisit** if that window becomes longer than the poll
    interval, or a page starts polling `/analysis`: then poll at `max(60 s, Retry-After)`.
- **F9 — a signed-in session silently became a guest after 15 minutes. Fixed 2026-10-08.** The
  access token lives `JWT_ACCESS_TTL_MINUTES` = 15, in memory only, and was renewed **only on
  mount**. On `/matches*` and `/analysis` `get_optional_user` treats an expired token as a guest
  (no 401), so the polling match page and the list switched to the guest view and counted views
  against the guest quota of the IP; routes behind `get_current_user` answered 401. Found while
  fixing it: on **every page load** a signed-in user's first request also went out as a guest
  (child queries start before `Providers` runs `hydrate()`), and nothing refetched afterwards.
  - **Now** (`lib/auth/store.ts`): the expiry is measured on the client (response time +
    `expires_in`); a timer renews 60 s early; `visibilitychange`, `online` and `focus` renew a
    late tab (throttled timers, sleep). `authHeaders()` is the only bearer source: it waits for
    the page-load restore, renews a token near or past expiry first (one refresh per tab, shared
    by every caller; 409 retried once), and with an expired token and a failed renewal throws
    `SessionRefreshError` — **nothing is sent as a guest**. A static test forbids reading the
    token synchronously elsewhere.
  - **No deadlock:** each attempt times out after 10 s for every waiter; a failure rejects all
    waiters at once; a settled attempt is never reused; network/5xx failures retry at
    5/15/30/60 s and on focus/online/visibility.
  - **What the user sees:** refresh 401 → signed out, header shows the login button and "Your
    session has expired — please sign in again." / «Сессия истекла — войдите снова.» — only if
    the tab held a session (a guest with a stale cookie on load sees nothing). Network/5xx →
    still signed in, "Can't renew your session — retrying…" / «Не удаётся обновить сессию —
    повторяем попытку…»; a match card keeps its data with the F8 note; promo redeem, backtest
    run and save, and push settings show "Couldn't renew your session…" / «Не удалось обновить
    сессию…»; the follow toggle and admin writes fail without being sent (the header notice
    explains it); mutations are not retried.
  - **Other tabs:** a logout is broadcast (`BroadcastChannel("betpulse-auth")`, logout only; tokens
    are not shared) and the other tabs sign out quietly — no "expired" notice. A login as another
    user in another tab moves the shared refresh cookie; the next refresh here returns that user,
    so the store takes it and every query is invalidated (header and data stay on one account).
  - **Cache:** after renewing an expired token the match and follow queries reload (free after
    F6: a match already viewed today costs nothing, the list and a cached analysis spend no
    quota); after an account change or a sign-out every query is invalidated.
  - `Cache-Control: no-store` on every BFF auth response (ER2-12).
  - **Known limitation — client clock moved backwards.** The expiry is client-measured, so a
    clock set back after a token was issued makes an expired token look valid; the public
    routes then serve the guest view until the next renewal. Not fixed (owner, 2026-10-08).
    **Option if it ever shows in practice:** the public routes answer an expired-but-genuine
    token (valid signature, past `exp`; not a garbage token) with a header such as
    `X-Auth-Expired: 1` on the normal guest response, and the client renews and refetches when
    it sees it.
  - **Tests:** Vitest (fake timers) for every path above, and `e2e/session-refresh.spec.ts`
    (Playwright, no backend: `page.route` + `page.clock`) for a real-browser renewal.
- **F7 — public request limits. Fixed 2026-10-09** (§6 "Public request limits",
  `SECURITY.md`). Before: the list had no limit, `/matches/{id}` and `/analysis` checked theirs
  after `get_tier_context` (two queries for a signed-in caller), `/auth/refresh` had no per-IP
  limit, and the client treated a refresh 429 like a 5xx (its own 5/15/30/60 s schedule, focus
  and visibility renewing regardless).
  - **Now:** list 120 / 60 s per caller; the three public match limits run in one dependency
    before tier resolution, identity from the verified token subject without a database read;
    refresh 120 / min per IP before the rotation, fail-open without Redis; the client keeps the
    session on a refresh 429 and waits `Retry-After` (clamped, with jitter), sending nothing
    before; the list and the analysis keep their data on a failed refetch (F8's rules), and the
    polls wait `Retry-After` after a 429.
  - **Checked for F8's rules:** `MatchList` and `AnalysisBlock` changed; `MatchDetailView` (F8)
    gained the `Retry-After` wait; `NotifyToggle`, `NotificationsSettings` and the admin views read
    `data` first, so a failed refetch already kept their data (the admin `IngestionView` shows its
    empty state, not an error, on a failed first load: cosmetic, left).
  - **Thresholds and shared addresses:** one page view is 1 list request, each new filter 1, the
    poll 1 a minute per tab, no focus refetch; a session costs a few refreshes a minute. 120 per
    address leaves room for an office NAT; a large carrier NAT with more than ~120 open guest tabs
    on one IPv4 address would see the list's 429 (the cards stay, the note says so); IPv6
    subscribers have their own /64 since F5. Both thresholds are settings.
  - **Tests:** `tests/api/test_public_rate_limits.py` (red before: the 121st list request 200,
    the 121st refresh 401, the detail limit after tier resolution); Vitest
    `lib/auth/refreshRateLimit.test.ts`, `lib/polling.test.ts`, `MatchList.test.tsx`, analysis and
    detail cases; `e2e/refresh-rate-limit.spec.ts` (red on the previous `store.ts`: a refresh
    went out inside the window).
  - Found while doing it: a signed token with a non-string `sub` was already a guest (PyJWT
    rejects it as `InvalidSubjectError`); the tests keep that.

### Found in the rc6 rehearsal (2026-10-08): F10–F17

Read-only investigations on the rehearsal stack (§9i, rc6 rehearsal); every fix starts with
the failing test named here.

- **F10 — a bootstrapped admin could not use the admin UI. LAUNCH BLOCKER. Fixed 2026-10-08.**
  `require_admin` (`core/deps.py`) needs `must_change_password = false` and, with
  `ADMIN_2FA_REQUIRED=true`, `totp_enabled`; `create-admin` sets `must_change_password = true`.
  The frontend had no password-change or TOTP screen and no TOTP step at login (the rc6
  rehearsal used `admin-setup-rehearsal.sh` instead). **Now:**
  - `/account/security` (any signed-in user; "Безопасность" in the header): password change,
    then an automatic sign-in with the new password (a change revokes every refresh token), with
    the TOTP code asked when the account has TOTP on; TOTP setup with a QR code (SVG drawn from
    `qrcode-generator`'s module matrix, no injected markup) and the manual key, confirmed by a
    code; turning TOTP off with a code.
  - The admin panel sends an admin to `/account/security` until the initial password is changed
    and, when the server requires it, TOTP is on. `UserOut.two_factor_required` (computed:
    admin and `ADMIN_2FA_REQUIRED`) lets the client follow the server instead of guessing.
  - Sign-in asks for the code only after the server answers 401 with `X-2FA-Required`
    (relayed by the BFF with `Retry-After`; no other backend header). A wrong code is answered
    like a wrong password ("Invalid credentials") and counts on the same per-account failure
    counter and lockout and the same per-IP window (5/min), so the code step is no faster to
    brute-force than the password (`tests/api/test_auth_account_security.py`). That the code
    step appears at all tells a caller the password was right — inherent to a two-step login;
    password guesses are limited by the same counters.
  - The TOTP secret is shown once and lives only in the setup component's state: never in
    storage, the URL or a log; every `/api/auth/*` answer is `no-store`. Leaving the page
    before confirming leaves TOTP off (the server activates it only on enable), and a new setup
    issues a new secret.
  - BFF routes `/api/auth/change-password` and `/api/auth/2fa/{setup,enable,disable}` go through
    `proxyAuth` with the bearer token from memory, so no CSRF token is needed (a cross-site page
    cannot attach it); the backend routes were already `U` in the route policy (#110).
  - **Lost authenticator:** `python -m app.cli reset-2fa --email … [--require-password-change]`
    (§9i, "`docs/DEPLOY_VPS.md`").
  - Wording: "Панель администратора" / "Admin panel" everywhere (was «Админка»); the audit
    subtitle says «действий администраторов». The privacy policy now says the encrypted TOTP
    secret is stored for anyone who turns 2FA on (mandatory for administrators).
- **F11 — `POST /auth/2fa/setup` silently reset an enabled TOTP. Fixed 2026-10-08.** `setup_totp`
  wrote a new secret and set `totp_enabled = False` without a code. Now it answers **409**
  `two_factor_already_enabled` and changes nothing; rotation is `disable` with a valid code, then
  setup and enable. A wrong code on enable/disable is audited in a transaction of its own
  (`auth.2fa.failure` used to be rolled back with the 400).
- **F12 — the 18+ gate is never remembered. LAUNCH BLOCKER. Fixed 2026-10-08.** The root layout
  (a server component) imported `AGE_GATE_COOKIE` from `components/legal/AgeGate.tsx`, a
  `"use client"` module. On the server every export of a client module is a client reference,
  so the layout got a function stub (`registerClientReference(…, "AGE_GATE_COOKIE")` in the
  built chunk), `cookies().get(stub)` never matched, `consented` was always `false` and the gate
  came back on every page load, although the browser stored `bp_age_ok=1` correctly (180 days,
  `path=/`, `samesite=lax`, set by the client). Present since the legal pages (`723a3a2`,
  2026-10-02), i.e. in every release; independent of domain and TLS, so every visitor on a VPS
  would have seen it. Vitest did not catch it (it imports the real module) and the e2e tests
  never checked the gate after a reload. Fix: the name lives in `lib/ageGate.ts` (no
  directive); `e2e/age-gate.spec.ts` checks the server HTML and the dialog after a confirm +
  reload and with the cookie preset; `lib/clientBoundary.test.ts` fails when a module without
  `"use client"` imports anything but a component from a client module, or a client module
  imports `next/headers`, `server-only` or `lib/server/*`. The same guard moved
  `MATCH_CARD_HEIGHT` to `components/match/matchCardLayout.ts` (no bug today: the skeleton is
  rendered only by the client `MatchList`).
- **F13 — an aborted refresh can end in family revocation and a forced logout.** Every page
  load posts `/api/auth/refresh`. If the server rotates the token but the response never reaches
  the browser (reload or navigation mid-flight, the client's own 10 s timeout aborting the
  fetch, a network drop), the new `Set-Cookie` never lands and the browser keeps the old token,
  which the server has already rotated; presenting it again more than
  `REFRESH_REUSE_GRACE_SECONDS` (10 s) later is treated as reuse and revokes the whole family
  (`services/auth.py`); within 10 s it gets 409 and still no new token. Seen once on the stand
  (rc5, 2026-10-08 16:20 UTC: one `auth.token.reuse_detected`, no `refresh_conflict`, last
  rotation 10 minutes earlier). Medium: no security loss, random logouts.
  **Decision (owner, 2026-10-08): option C, two PRs, A first; pre-launch group, not now.**
  - **A (client) — done 2026-10-09.** The refresh is sent with `keepalive: true` and no abort
    signal (`lib/auth/store.ts`); at the 10 s timeout the waiters give up (same UI and retries
    as before) but the request runs on, and a late 200 is applied unless the session ended or
    another answer was applied since (`applyLate`). A reload during a refresh still in flight on
    the server meets the existing 409 path (the new page presents T1 while T2 is fresh) and
    renews with T2 once the keepalive answer has landed. Tests: Vitest (keepalive, no signal,
    late success applied / dropped after logout) and `e2e/refresh-reload.spec.ts`, which drives
    a reload storm through the real BFF against the test-only fake backend: red on the previous
    `store.ts` with the stand's sequence (rotated, 409, 409, reuse), green after, and the
    cookie jar ends on the family's live token, never T1. **Verified in Chromium only; Firefox
    (keepalive since 133) and Safari behave so by specification, not by a test.**
  - **Left for B:** a network or response drop after the server committed (mobile networks,
    proxies); the whole browser closed or crashing, or a mobile OS unloading the tab; an answer
    slower than the 10 s reuse window, with a retry on the old cookie after the window;
    browsers without keepalive (Firefox before 133).
  - **B (server) — done 2026-10-09.** At rotation T1 → T2 the server keeps T2
    Fernet-encrypted in Redis for **60 s**, keyed by T1's hash. A re-presentation of T1 inside
    the window, while T2 is unused, the account active and unchanged, **and** the user-agent
    hash and the client subnet (IPv4 /24, IPv6 /64) match the rotating request, gets **the
    same T2** back with a new access token — no new refresh token; at most 3 times per T1 and
    10 per client IP a minute. Anything else follows the earlier logic (409 inside 10 s,
    family revocation after). Details, refusal reasons and settings: §6 "Refresh replay". This
    covers every case "left for B" above that reaches the server again within 60 s from the
    same browser and network.
  - **B depends on F5. Do not rely on it as a theft control before F5 is verified on the server**
    (F5 is fixed in code since 2026-10-09: with the dual-stack network the binding sees real /24s
    and /64s; on Docker Desktop every client is still the gateway).
    Until F5 is fixed, every IPv6 client on a VPS reaches the API as the Docker bridge gateway,
    and on Docker Desktop every client does, so all of them share one "subnet" and the binding
    reduces to the user-agent match — which a thief holding T1 can copy. In that state a T1
    stolen and presented within 60 s while T2 is unused gets T2 instead of revoking the family.
    The exposure is narrow (60 s, 3 uses, T2 still unused) but real. **Owner decision
    (2026-10-09): the replay stays on by default.** F5 is a launch blocker, so no public
    deployment runs before it (a closed trial run only behind the provider firewall, §9i
    launch blockers), and rc7 must exercise the enabled path. Detection is delayed, not lost:
    once either party rotates T2, the other's next presentation of a rotated token follows the
    ordinary rules — 409 while its direct successor is live and ≤ 10 s old, family revocation
    otherwise — unless it again qualifies for a replay (same fingerprint, inside 60 s, under the
    cap). Verify the binding on the first VPS (§9i, first-VPS checklist).
- **F14 — the header flashed "Sign in" before the session was restored. Fixed 2026-10-08.**
  `AuthMenu` rendered the signed-out state whenever `user` was null, including while `hydrated`
  was false and in the server HTML. It now renders a neutral placeholder of the same size until
  the page-load restore settles.
- **F15 — the F8 stale note shows the time in UTC** ("17:22" for a viewer at UTC+4). next-intl's
  time zone is pinned to UTC app-wide (`i18n/request.ts`) so server and client render kickoff
  times identically; the note only appears in the browser, so it can use the viewer's time
  zone. Whether kickoff times should also move to local time is a separate product decision.
  First failing test: with a UTC+4 zone, a refetch failure after an update at 17:22Z shows
  "21:22". Slot: cleanup PR.
- **F16 — the follow ("Notify me") toggle is offered on finished matches.** `NotifyToggle` has
  no status check and `follow_match` accepts any fixture; a finished match never swings. Fix:
  hide it for `finished` and answer 409 on the server. First failing tests: Vitest (no toggle
  for `finished`) and pytest (PUT follow on a finished fixture → 409). Slot: cleanup PR.
- **Not a defect — the market is not a method bar.** `GET /matches/{id}` excludes `market`
  and `consensus` from `methods` and serves the market separately; the UI shows it only as
  "Delta vs market", which needs a consensus. Without a consensus (today: under 200 matches)
  the market reference is shown nowhere. The guest's four blurred rows are a fixed placeholder,
  not data. Whether to show the market on its own is a product question (backlog).
- **F18 — logout ends the refresh family, not the access token.** `POST /auth/logout` revokes
  the tab's refresh family; the access token in memory is dropped by that tab and by the other
  tabs of the same browser (BroadcastChannel), but the token itself stays valid until it expires
  (≤ 15 minutes) if it was copied elsewhere, and sessions on other devices are not touched
  (another family). Low: it needs a stolen access token, which expires on its own. Options if it
  matters: bump `credentials_changed_at` on "log out everywhere" (a new button) — the same
  mechanism as ER2-01 — or a short deny-list of logged-out `jti`s in Redis until their `exp`.
  Slot: with O2.
- **F17 — an accepted TOTP code can be used again within its window.** `verify_totp` checks
  `valid_window=1` (the current 30 s step ± one) and remembers nothing, so a code that just
  signed someone in is accepted again for up to ~90 s, at login and on enable/disable. Low: a
  replay needs the password too (or a live session for disable) and a code observed or relayed
  within that minute and a half, and the failure counters still apply. Fix: remember the last
  accepted time step per user (a column, or Redis `SET NX` with a ~2-minute TTL) and refuse a
  code from that step or an earlier one. First failing test: the same code twice in one step →
  the second login is 401. Slot: with O2 (auth hardening), or earlier if 2FA becomes common.

### External review 2 (2026-10-07) — verified backlog

Two static-analysis reports (no tests run). Every finding was **verified by reading the code at
main `314029b`**; line numbers will drift. Labels `ER2-` keep them apart from the first review's
`ER-` items. Items marked *no action* are recorded so they are not raised again.

| ID | Verdict | Evidence | Severity | Smallest fix | First failing test | Slot |
|---|---|---|---|---|---|---|
| ER2-01 | **Fixed 2026-10-09** (`credentials_changed_at`, §6) | `get_current_user` checks only signature, expiry and `is_active` (`core/deps.py:49-79`); `change_password` revokes refresh tokens only (`services/auth.py:589-593`); no `token_version` / `password_changed_at`. An issued access JWT stays valid ≤ 15 min after a password change. Not routed by the BFF today | Medium before a password-change UI (**there is one since F10**) | `users.password_changed_at`; reject tokens with `iat` before it in `get_current_user` and `get_optional_user` | token issued before a password change → 401 on `/auth/me` | done |
| ER2-02 | **Fixed 2026-10-08 (with F10)** | `POST /auth/change-password` had no rate limit or failure counter (`api/auth.py:235-256`); with a stolen access token the current password can be brute-forced, each try an Argon2 hash | Medium | per-user fixed window (`counters.incr_with_ttl`) **before** `verify_password` | 6th wrong try → 429, `verify_password` not called | done: §6, "Account security limits" |
| ER2-03 | Confirmed | `register_user` checks then inserts (`services/auth.py:142-153`); `users.email` is unique; no `IntegrityError` handler (`main.py:62` registers only the validation handler) → a concurrent duplicate is a 500. Not routed today | Low | catch `IntegrityError` on the flush → `EmailAlreadyRegistered` (after O2: the same answer either way) | two concurrent registrations of one address → no 500 | O2 |
| ER2-04 | Confirmed (doc gap) | O2 / ER-M-04 did not say where the limit runs; `register_user` hashes at `services/auth.py:148` | Medium (CPU DoS through Argon2) | limit as a dependency before the handler body; recorded in O2 and ER-M-04 | over the limit → 429, `hash_password` not called | O2 |
| ER2-05 | **Done 2026-10-10** | no `statement_timeout`, `lock_timeout` or `idle_in_transaction_session_timeout` on any engine (`core/db.py:30-79`) | Medium; **launch blocker** | see below | API session: `pg_sleep` past the limit fails; a worker session does not | pre-launch protection group (with F7) |
| ER2-06 | Confirmed, wider | `push_task` keeps one session for the whole dispatch (`workers/tasks.py:185-191`): the first SELECT opens a transaction that stays idle across each HTTP send (Web Push 10 s, Telegram 15 s) and the 30 s retry sleep (`services/live/push.py:190-224`). `autoflush=False`, so pruned rows are not flushed and no row lock is held — but the connection is. Pool 5+10 vs `max_jobs=20`. **New:** `job_timeout=60` with `push` `max_tries=1` (`workers/arq_app.py:120-129`) — one unreachable subscription costs up to 50 s (Web Push) / 60 s (Telegram), so **two unreachable subscribers kill the whole push job**: the remaining followers get nothing and the prunes are lost (no commit); the budget reservations are released by `finally` | Medium now (live is dev-only); High once live is public | read followers and tiers in a short session and close it; deliver without a session; prune in a short session; retry via a deferred ARQ job instead of `sleep`; bounded concurrency | `checkedout() == 0` during a stubbed send; N unreachable followers finish within the job timeout | live → Sportmonks (with ER-H-01, ER-M-02); related to ER-H-05/ER-H-06 |
| ER2-07 | Confirmed | worst case per unreachable subscription `2T + 30 s` (T = 10 s Web Push, 15 s Telegram), sequential: ≈ N · k · (2T + 30) for N followers with k subscriptions each; a successful send is ~0.1–0.5 s | as ER2-06 | with ER2-06 | with ER2-06 | with ER2-06 |
| ER2-08 | **No action** | the `IntegrityError` on the redemption insert (`services/promo.py:235-238`) becomes `AlreadyRedeemed`, the router raises at once (`api/promo.py:174-177`), and no statement runs in that session afterwards; `get_session` rolls back, which also returns the claimed activation — correct | none | — | — | — |
| ER2-09 | Confirmed, measured | the generation loop is synchronous until the final flush (`services/promo.py:147-164`), so the **whole API stalls** while it runs; api memory limit 1 GB. Measured on Postgres in a Linux container: 10k codes 2.6 s / 37 MiB Python peak / 138 MiB RSS; 50k 12.2 s / 164 / 405; 100k 24.5 s / 328 / 744 | Medium | `BATCH_MAX = 10_000` (service and schema `le=`) | `size=10_500` → 422 | cleanup PR |
| ER2-10 | Confirmed | admin batch list unpaginated (`api/promo.py:80-89`); `export.csv` loads the whole batch as ORM rows (`api/promo.py:117-153`). With `BATCH_MAX` 10k an export is a few MB; batches are few | Low | `limit`/`offset` on the list; streamed export later | list with `limit` → exactly `limit` rows | later |
| ER2-11 | **No action** (intended) | the promo limiter counts every attempt, refused ones included (`services/limits.py:44-56`) — right for a brute-force limit; the key is bucketed by clock hour, so counting a 429 never extends the lockout | — | — | — | — |
| ER2-12 | Confirmed | `proxyBackendGet` dropped every backend header, so `Retry-After` from a 429 on `/matches/{id}` and `/analysis` never reached the browser — **fixed with F8**. The backend sets no `Cache-Control` on auth responses, so `authProxy.ts` loses nothing, but the token responses (`/api/auth/login`, `/refresh`) carry no `Cache-Control: no-store` (RFC 6749 §5.1) | Low | `no-store` on token responses | BFF refresh response → `cache-control: no-store` | `Retry-After`: F8 (done); `no-store`: F9 (done) |
| ER2-13 | Confirmed | read nowhere — `Settings` fields: `PUBLIC_BASE_URL`, `DEFAULT_LOCALE`, `CONSENSUS_WEIGHT_MODE` (replaced by the DB, §9g), `csrf_header_name`; `.env.example` only: `POSTGRES_HOST`, `POSTGRES_PORT`, `FOOTBALL_DATA_COUK_ENABLED`, `API_FOOTBALL_DAILY_LIMIT`, `API_FOOTBALL_PER_MINUTE_LIMIT`, `ENABLED_LEAGUES`, `MODEL_RETENTION_VERSIONS`, `RETRAIN_CRON` (§9k), `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_DAILY_TOKEN_BUDGET`, `LLM_CACHE_TTL_SECONDS` (LLM config lives in the DB), `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `BACKUP_ENABLED`, `BACKUP_CRON`, `BACKUP_RETENTION_DAILY`/`_WEEKLY`/`_MONTHLY`, `BACKUP_ENCRYPTION_PUBLIC_KEY`, `RESTORE_DRILL_CRON`, `RPO_ALERT_MINUTES` (14b placeholders), `LOG_LEVEL`, `LOG_FORMAT`, `SENTRY_DSN`, `METRICS_ENABLED` (no `/metrics`), `AGE_GATE_ENABLED`, `DISCLAIMER_REQUIRED`. Read after all: `CORS_ALLOWED_ORIGINS`, `WEBPUSH_CONTACT_EMAIL` (via properties), `SPORTMONKS_API_TOKEN`, `THE_ODDS_API_KEY` (`providers/recording.py`), `TZ` (the containers' libc, via `env_file`) | Low | remove from `.env.example` and `Settings`, or wire them; mark 14b placeholders as such | every `.env.example` key is read somewhere (allowlist for placeholders) | cleanup PR |
| ER2-14 | Already tracked | backtester unbounded fetch, in-memory structures, response size | — | ER-H-07 | — | — |
| ER2-15 | Already tracked | `season_split` is a breakdown, not walk-forward | — | §9d | — | — |
| ER2-16 | Already tracked | a push reservation is lost on a crash between reserve and release | — | §6 (Redis counters) | — | — |
| ER2-17 | Confirmed, new | saved strategies have no per-user cap and their list is unpaginated (`api/backtester.py:54-95`, expert only); the admin providers list is unpaginated but tiny (`api/providers.py:39`) | Low | per-user cap (e.g. 100) and `limit`/`offset` | 101st save → 409 | later |

**ER2-05 — database timeouts (launch blocker; done 2026-10-10, §6 "Database timeouts").**
- **API processes only:** `statement_timeout = 15s`, `lock_timeout = 5s`, set per connection
  (`connect_args={"server_settings": …}`) on the request, security and read engines.
- **Enabled from `create_app()`**, not from the `api` service's environment: the engines are shared
  code, and the CLI runs as `compose run api python -m app.cli …`, which would inherit it.
- **Exempt:** Alembic (its own engine in `migrations/env.py`), every ARQ worker (training,
  ingestion, live recompute, push), the CLI (`bootstrap-history`, backfills, `data-report`,
  `replace-source`). The backtester runs inside an API request (≈13k rows today) and stays under
  15 s; if it grows, `SET LOCAL statement_timeout` inside it rather than a global exemption.
- **`idle_in_transaction_session_timeout = 60s` only after ER-H-05:** `/analysis` keeps its
  transaction open for the whole LLM call (the OpenAI SDK's default timeout is 600 s), and an open
  SSE stream holds one too (ER-H-06); 60 s would kill both.

### Queue (owner, 2026-10-09)

1. **F8**, **F9**, **v0.0.1-rc6**, **F12**, **F10 + F11 + F14**, **ER2-01** — done.
2. **v0.0.1-rc7** — the owner, by hand; checklist by the agent (§9i, "v0.0.1-rc7 (planned)").
3. **F13** — parts A and B done (2026-10-09; §9m). B relies on F5 for its subnet binding (F5 in code
   2026-10-09; server verification pending).
4. **Launch blockers:** F5 (in code; verify on the server), `docs/DEPLOY_VPS.md` (outline in
   §9i). ER2-05, Redis `requirepass`, memory policy and persistence: done 2026-10-10.
5. **Cleanup PR:** ER2-09 (`BATCH_MAX` 10 000), ER2-13 (dead settings), F15 (stale-note time in
   the viewer's time zone), F16 (no follow toggle on finished matches).
6. **O2** (registration) with ER2-03, ER2-04, ER-M-04, F17 and F18.
7. **Live to Sportmonks** with ER2-06/ER2-07, ER-H-01, ER-M-02.
8. **Later:** ER2-10, ER2-17.

## 10. How to resume

1. Read this file + the spec §14 for the current phase.
2. `git fetch origin` and check whether the current phase branch's PR has merged. If merged and
   there is follow-up work, restart the branch from fresh `origin/main` (do not stack on merged
   history).
3. Confirm the previous phase is green in CI before starting the next one.
4. Post the phase plan, wait for "go", then implement → tests → CI green → PR. The owner merges.

## 11. Parked work (owner-requested, not yet scheduled)

- **Dependabot: security alerts and security updates are OFF (owner action).**
  - The repository settings have Dependabot alerts disabled; the API answers 403 "Dependabot
    alerts are disabled for this repository". So no PR is ever labelled a security update.
  - The owner should enable *Dependabot alerts* and *Dependabot security updates* in Settings →
    Code security.
  - Until then, the blocking dependency-audit gates in CI are `pip-audit` and the prod-only
    `npm audit`. Both were clean on 2026-10-04, apart from the tracked dev-only `braces`. The
    security workflow also runs gitleaks, Bandit, Semgrep and Trivy on every PR.
- **Dependabot grouping (#81).**
  - Minor and patch bumps arrive grouped per ecosystem; each major arrives as its own PR.
  - Ignored on purpose: redis-py majors (arq), `@types/node` majors, Python minor/major and Node
    major in the base images.
  - CI pins its own Timescale and Caddy images (`ci.yml`, `scripts/caddy-smoke.sh`). Bump them in
    the same PR, or CI stays green without testing the new version.
  - Review of 2026-10-04:
    - #80, #52, #77, #4 and #8 were closed; they will be re-proposed under the new grouping.
    - #75 and #76 are fine to merge.
    - #6 is deferred.
    - #5, #2 (verify with an rc release publish) and #78 (Caddy + CI pin) come after the first
      successful rehearsal.
    - #79 (Timescale 2.30.2-pg16) comes before the first launch, as its own PR with the CI pins:
      done in `chore/timescale-caddy-bump`, together with Caddy 2.10. Dependabot's #83 was closed in
      its favour.
- **Majors that need their own migration plan, after the first launch.**
  - Each one: separate branch, Step 0, and a written before/after check.
  - **pandas 3** (copy-on-write by default, string dtype). It touches every feature builder, so it
    needs a **before/after comparison of the ML feature tables and the evaluation metrics** (same
    data, same seeds) before it can merge.
  - **Next 16**, together with `eslint-config-next` 16 and ESLint 10.
  - **TypeScript 7**: typescript-eslint does not support it yet.
  - **Python 3.14** in the base images: wheels for numpy, pandas, scipy, LightGBM, statsmodels and
    MLflow.
  - **Node 26**: wait for LTS, then verify that Next supports it.
  - **Redis 8** server: also check the licence.
  - **openai 3**: the LLM client API.
  - **redis-py 8 together with an arq upgrade**: arq 0.28 pins `redis<6`.

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
  - multi-season history import, the prerequisite for LightGBM/consensus to reach champion size;
  - **online inference** for upcoming fixtures: today `Prediction` rows are written only by training
    (finished fixtures), so pre-match cards for upcoming matches need a scoring job that loads the
    registered model (by `mlflow_run_id`) and predicts before kickoff — estimate ~5–7 days; tracked
    as **ER-C-01** in §9m, which also requires scheduled fixtures to become visible in `/matches`;
  - **Web Push payload**: `send_webpush` posts an empty body (no RFC 8291 payload encryption), so
    the service worker never gets the fixture id and always shows its generic, localized text.
    Sending the id (encrypted) would let it render the match and the baseline numbers. The snapshot
    it would need was removed in audit A3; see §9f for the conditions to bring it back;
  - **team-aware live base rates** from the running Dixon-Coles fit (replacing the fixed
    `get_base_rates`), after which the live label can change from `live_baseline`.

- **Before registration is opened to the public** (audit 1b, O2; also **ER-M-04** in §9m): `POST /auth/register` has no
  rate limit and answers `409 Email already registered`, which lets anyone check whether an
  address has an account. Before any sign-up form or BFF route exposes it:
  - add a per-IP and per-email rate limit, **enforced before `hash_password`** (Argon2 is
    deliberately expensive: without that order every refused request still costs a hash — a
    CPU denial of service; ER2-04);
  - answer the same way whether or not the address exists (for example `202` plus an email to
    the address);
  - test both.
  Today neither the BFF nor Caddy routes to it.

- **Auth hardening follow-ups** (found during the auth-transactions PR; separate small PRs):
  - *Lockout as a victim-DoS vector.* The per-account backoff (`LOGIN_MAX_FAILURES`, then
    `LOCKOUT_BASE_SECONDS·2ⁿ` capped at `LOCKOUT_MAX_SECONDS`) is keyed on the account only, so
    anyone who knows an email can keep that user locked out. Consider a shorter cap, keying the
    backoff on account + client bucket, or a challenge (CAPTCHA/email link) instead of a hard lock.
  - Still open: backtester memory (M-02); LLM issues (H-02, L-01), including the LLM daily token
    budget in `app/services/llm/analysis.py`, which still does a non-atomic `INCRBY` + `EXPIREAT`
    (dated key, so a leak rather than a lockout) — move it onto `app.services.counters` with the
    LLM PR. The 2026-10-05 external review tracks these as **ER-H-07** (backtester) and
    **ER-H-02…ER-H-05** (LLM) in §9m.

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
