# Data Sources

How this project gets football data: what each source is for, how to obtain access, where to put the
key, and what the request budget looks like.

> **Rule:** provider API keys are entered in the **Admin UI** (Admin → Providers → Add provider).
> They are encrypted at rest (Fernet, `DATA_ENCRYPTION_KEY`) and never returned to the browser:
> only a masked 4-character suffix is shown. The keys in `.env` are a fallback.
> **Current state:** the stored key is not read yet; the live poller uses `API_FOOTBALL_KEY` from
> `.env`. Provider PR 1 adds the lookup (DB key first, `.env` fallback).
> A database dump holds only ciphertext. `DATA_ENCRYPTION_KEY` lives in the server `.env` and must
> be backed up **separately** from the dumps (see HANDOFF §9l).

---

## The two data planes

We deliberately use **two different sources**, because no single one does both jobs well.

| Plane | Purpose | Production source | Dev / local |
|---|---|---|---|
| **Historical** | ML training, calibration, strategy backtester, ROI/CLV | Sportmonks (fixtures, results, stats) + The Odds API (closing odds) — §3, §4 | football-data.co.uk (free CSV, never in production) |
| **Live / upcoming** | Fixtures, in-play state, odds, stats | API-Football today; Sportmonks later; forward odds from The Odds API | same |

Owner decisions, licence status and the PR order: HANDOFF §9l.

Roles are assigned per provider in the admin UI: `historical`, `live`, `odds`, `xg`. A provider can
hold several roles. Ingestion picks the **highest-priority provider that has the required role and
remaining quota**.

---

## 1. football-data.co.uk — historical, **dev / local only** (role: `historical`, `odds`)

**Provider id:** `football_data_couk` (implemented in `backend/app/providers/football_data_couk.py`).

> **Not for production.** The site grants no licence for commercial use, so the adapter is
> `licensed_for_production = False` and the ingestion core refuses it when `ENVIRONMENT=production`
> (CLI exits 2, the admin re-scan answers 409). Use it only for local / dev data. The production
> historical sources are **Sportmonks** (fixtures, results, stats) and **The Odds API** (odds),
> §3–§4. Each plugs in as a new `SourceAdapter` without schema changes.
> Any football-data rows found in production are replaced by a dedicated `replace-source`
> operation, not by the backfill alone (HANDOFF §9l, provider PR 2b). It runs after the Sportmonks
> backfill and before the Odds API backfill. It overwrites the fixture fields with Sportmonks
> values, deletes the football-data odds and refs, and STOPs on any fixture without a Sportmonks
> counterpart.

**What it is.** Free CSV archives of European league results, going back 30+ seasons. Each row has
full-time and **half-time** scores, shots, corners, cards — and **closing odds from 10+ bookmakers**
(Pinnacle, Bet365, William Hill, …).

**Why it is the backbone.** Without closing odds we cannot compute ROI or CLV, which means the
strategy backtester and the market-implied benchmark would be impossible. This dataset bootstraps
the whole ML layer for free.

**Access.** No registration, no API key. Plain CSV over HTTPS
(`https://www.football-data.co.uk/mmz4281/{season}/{code}.csv`, e.g. season `2324`, code `E0`).

**Coverage / league codes.** Major European divisions. Our canonical → football-data division map:

| Canonical | football-data code |
|---|---|
| EPL | E0 |
| LALIGA | SP1 |
| SERIEA | I1 |
| BUNDESLIGA | D1 |
| LIGUE1 | F1 |

**Does not cover RPL.** football-data.co.uk has **no** Russian Premier League data. Passing `RPL`
to `--leagues` is **not** silently skipped — the ingester logs a structured `WARNING`
(`event: league_unsupported_by_source`) and moves on. RPL history comes from the live provider and
is flagged **beta** in the UI.

**Column mapping (as implemented).** The loader (pandas) reads these columns. Each quote set is read
independently: a missing closing set is never filled from pre-closing columns, and a set with any
price outside `1 < price <= 1000` is skipped:

| Field | CSV column(s) |
|---|---|
| kickoff date / time | `Date` (dayfirst), optional `Time` |
| full-time goals | `FTHG`, `FTAG` |
| half-time goals | `HTHG`, `HTAG` |
| shots / on target | `HS`,`AS` / `HST`,`AST` |
| corners | `HC`, `AC` |
| **Pinnacle closing 1X2** | `PSCH`,`PSCD`,`PSCA` (closing, `ts = kickoff`) |
| Pinnacle pre-closing 1X2 | `PSH`,`PSD`,`PSA` (**not** closing, `ts = kickoff − 1 day`) |
| **Market-average closing 1X2** | `AvgCH`,`AvgCD`,`AvgCA` → `bookmaker='market_avg'` |
| **Pinnacle closing O/U 2.5** | `PC>2.5`,`PC<2.5` (closing) |
| Pinnacle pre-closing O/U 2.5 | `P>2.5`,`P<2.5` (**not** closing, `ts = kickoff − 1 day`) |
| **Market-average closing O/U 2.5** | `AvgC>2.5`,`AvgC<2.5` → `bookmaker='market_avg'` |

Each column set is stored independently in the `odds` hypertable: closing quotes with
`ts = kickoff`, `is_closing = true`; **pre-closing quotes are never stored as closing** —
`is_closing = false` at `ts = kickoff − 1 day`. That timestamp is an **approximation**:
football-data does not publish when a pre-closing quote was taken (its notes say Friday/Tuesday
afternoon before weekend/midweek games); the day only orders it before the closing quote. A
fixture that has only pre-closing quotes has **no** closing quote, so it is left out of "ROI vs
closing", the market model and the backtester. The 1X2 market is `market='1x2'`,
`outcome in {home,draw,away}`, the goals total `market='ou_2.5'`, `outcome in {over,under}` (it
feeds the backtester's totals bets, spec §6).

**Reference bookmaker.** Which bookmaker's closing quote is "the" closing price depends on the
kickoff date (`REFERENCE_BOOKMAKERS`): Pinnacle for kickoffs **before** 2025-07-23, and the market
average (`market_avg`) **from** 2025-07-23 on (that day included), because football-data warns that
Pinnacle's feed is unreliable from that date. `until_date` is exclusive, `from_date` inclusive.

**ID mapping (seed behaviour).** football-data.co.uk is a **seed source** (`may_seed_canonical`): the
first time a team/league name is seen it creates the canonical `teams`/`leagues` row (teams keyed by
`(country, normalized name)`, leagues by code) and records the alias in `provider_team_aliases` /
`provider_league_aliases`. Each creation emits a structured-JSON `WARNING` with `league`, `raw_name`,
`normalized` and `csv_row` so it is auditable — never a silent duplicate. **Other** providers
resolve against these aliases via a strict resolver (stable team id first, then name) that **raises**
`UnmappedEntityError` on an unknown name and records it in `provider_unmapped_teams`; list them with
`python -m app.cli unmapped-teams` and bind them with `python -m app.cli map-team` (audited).

**Identity.** The CSVs have no match ids, so each row gets a synthetic one
(`fd:{division}:{season}:{YYYYMMDD}:{home}:{away}`) in `fixture_external_refs`; a re-run finds it.
A match another source already filed (same league and teams within ±36 h, any season label) is
linked, not duplicated.

**How to load.**

```bash
make bootstrap-history HISTORY_ARGS="--leagues EPL,LALIGA --seasons 2022-2023,2023-2024"
make verify-history    HISTORY_ARGS="--leagues EPL,LALIGA --seasons 2022-2023,2023-2024"
```

`bootstrap-history` downloads from football-data.co.uk (local dev / VPS). `verify-history` prints a
`league | season | fixtures | odds` table and exits non-zero if any configured league/season has zero
fixtures. Ingestion is **idempotent** — re-running the same CSV inserts nothing new (external ref
lookup, then `ON CONFLICT DO NOTHING` on the fixture and odds identity keys).

**Caveats.**
- CSV `Date`/`Time` are UK local time; they are converted to UTC (GMT/BST aware, `zoneinfo`). Seasons
  before 2019-20 have no `Time` column: those rows are stored at **12:00 UTC** of the date with
  `kickoff_time_known = false`, and no time is ever shown for them.

**xG coverage caveat.** football-data.co.uk has **no shot coordinates**, so the own-xG model
(`backend/app/ml/xg.py`) runs in **approximate** mode for historical seasons: team xG is estimated
from shots / shots-on-target counts rather than per-shot geometry. `XgModel.data_quality` returns
`DataQuality.APPROXIMATE` in this mode and `DataQuality.FULL` only when a shot-level source (with
coordinates) is connected. Connecting such a source later is a new provider + `has_coordinates=True`
— no model rewrite. Provider xG (e.g. from API-Football) is inconsistent per league/season and is at
most a secondary feature, never the source of truth.

**Legal.** Free for personal/non-commercial analysis; check the site's terms before commercial use
and attribute the source. The committed test fixture
(`backend/tests/fixtures/football_data/E0_2324.csv`) is a tiny slice with an attribution header.

---

## 2. API-Football (api-sports.io) — live (roles: `live`, `odds`, optionally `xg`)

**Provider id:** `api_football` (interface, response parsers and live ingestion in
`backend/app/providers/api_football.py` + `backend/app/services/live/`).

**Live pipeline (Phase 5).** A self-rescheduling ARQ task on the `live` queue polls
`/fixtures?live=all` every `LIVE_POLL_INTERVAL_SECONDS` under a Redis single-flight lock,
quota-guarded (hard stop before the request when the day's budget is spent). Each in-play fixture
is resolved **strictly** through the ID-mapping aliases — an unmapped team/league is logged as a
structured `live_fixture_unmapped` warning and skipped, never guessed (alias seeding for
API-Football is an admin task). After each poll a per-fixture recompute (Dixon-Coles conditioned on
the current score + elapsed minute) runs **only when the score/minute changed**, writes
`predictions_live`, and appends a `live_updates` event (its `BIGSERIAL` id is the SSE
`Last-Event-ID`). A probability swing over `PROBABILITY_SWING_PUSH_THRESHOLD` enqueues a push
(Telegram Bot API + Web Push/VAPID), rate-limited to one per (user, fixture) per
`PUSH_RATE_LIMIT_SECONDS`.
Browsers subscribe over SSE at `GET /live/stream` (Pro/Expert tiers only); Redis pub/sub fans
updates out across API replicas.

**What it is.** Football-only REST API: 1200+ leagues, live updates every ~15 seconds, endpoints for
fixtures, standings, players, statistics, lineups, live scores, **odds**, transfers. All endpoints
are available on every plan — only request volume differs.

**Pricing (verify current values on their site before subscribing).** Free tier ≈ 100 req/day;
paid tiers start around $19/month for ~7,500 req/day, with higher tiers for 75k+/day. Direct via
api-sports.io or via RapidAPI (same pricing).

**Which plan we assume.** The entry paid tier (~7,500 req/day) is sufficient for our launch coverage
(top-5 + UEFA + RPL) — see the budget below.

**How to get the key.**
1. Register at api-sports.io (or RapidAPI).
2. Subscribe to the plan you need; copy the API key from the dashboard.
3. In our app: **Admin → Providers → Add provider → API-Football**.
4. Paste the key, assign roles (`live`, `odds`), set **daily limit** and **per-minute limit** to match
   your plan, set priority.
5. Save. The key is encrypted immediately; you will only ever see the last 4 characters again.

**Endpoints we call and the request budget.**

| Job | Endpoint | Cadence | Requests/day |
|---|---|---|---|
| Live state (all in-play matches in **one** call) | `/fixtures?live=all` | every 60 s | ~1,440 |
| Upcoming fixtures | `/fixtures?date=` | 4×/day | ~30 |
| Pre-match odds | `/odds` | 3×/day per fixture window | ~300 |
| Post-match stats/lineups | `/fixtures/statistics`, `/fixtures/lineups` | after each match | ~150 |
| Reference data (teams, leagues, standings) | `/teams`, `/standings` | daily, cached | ~50 |
| **Total** | | | **≈ 2,000 / day** |

Comfortably inside a 7,500/day plan, with headroom for retries and backfills.

**Quota behaviour.** Every call is counted in Redis against the admin-set limits. On exhaustion the
ingestor performs a **hard stop** (never overspends), logs it, alerts via Telegram, and falls back to
the next provider with the same role — if none exists, the affected data simply goes stale and the UI
shows a "data delayed" badge rather than serving wrong numbers.

**Coverage caveats.**
- Top-5 European leagues have rich data (lineups, player stats, sometimes xG).
- Smaller leagues and lower divisions frequently lack lineups and detailed stats.
- **Provider xG is inconsistent** across leagues/seasons/plans. Do **not** treat it as a primary
  feature. Our own shot-based xG model is the source of truth; provider xG is at most a secondary
  feature, and only after verifying the exact league + season + endpoint you rely on.
- **RPL history** is shallower than football-data.co.uk's coverage of the top-5. RPL models are
  therefore flagged **beta** in the UI until enough seasons accumulate.

**Legal.** Commercial use per their ToS. Odds availability is not guaranteed for every fixture. Logos
and media are copyrighted by their owners.

---

## LLM analysis provider (spec §8) — not a data plane

The LLM that writes the plain-language match analysis is **not** a football-data provider and is kept
separate from `provider_accounts`: it does not ingest data, it only *explains* the model outputs (it is
never a source of probabilities). It is any **OpenAI-compatible** chat-completions endpoint.

- **Config lives in `llm_config`** (a single admin-managed row), edited in the admin UI / `PATCH
  /admin/llm-config`: `base_url`, `model`, the API key (**encrypted at rest**, only a `••••1234`
  suffix ever shown — the full key is never logged), `max_tokens`, `daily_token_budget`,
  `cache_ttl_seconds`, `cost_per_1k_in` / `cost_per_1k_out`, and `is_enabled` (default **off**).
- **Budget & cost.** Generation hard-stops for the UTC day once `daily_token_budget` is spent
  (`budget_exhausted` with a reset time); token counts and computed cost are stored per analysis for
  the admin spend view.
- **Prompt is English-only**; the response language follows the caller's locale (`?language=ru|en`).
- **No key committed anywhere** — like every provider key, it exists in the DB (encrypted) or, for
  local dev / CI only, as an env fallback.

## 3. Sportmonks — production fixtures, results, stats (roles: `historical`, later `live`)

**Status.** Chosen (Growth plan); not implemented yet (provider PR 2).
`licensed_for_production = False` until the owner has Sportmonks' written confirmation: their ToS
allow commercial use and storage and forbid direct resale, but say nothing about ML training.

**Plan.** Growth: €99/month (€79 yearly), any 30 leagues, 2,500 calls **per entity per hour**,
14-day trial. ([plans](https://www.sportmonks.com/football-api/))

**API facts we rely on** (Sportmonks v3; docs read 2026-10-03)

*Access and limits*
- **Base and auth.** Base `https://api.sportmonks.com/v3/football`; the token goes in the
  `Authorization` header (`api_token` in the query also works — we do not use it).
  ([getting started](https://docs.sportmonks.com/football/welcome/getting-started))
- **Rate limit** is per entity: `/fixtures/123` and `/fixtures/between/...` share the Fixture bucket.
  - Every response carries `rate_limit {resets_in_seconds, remaining, requested_entity}`.
  - A 429 carries `retry_after`.
  - ([rate limit](https://docs.sportmonks.com/v3/api/rate-limit))
- **Errors:** 400 (`message`, `errors`), 401, 403 "not available in your current subscription"
  (`plan_required`), 404, 429, 500 (`error_id`).
  ([error codes](https://docs.sportmonks.com/v3/api/error-codes))

*Fixtures*
- **Endpoint:** `GET /fixtures/between/{start}/{end}`, YYYY-MM-DD, at most **100 days** per call,
  `per_page` ≤ 50.
  - Filter by league with `filters=fixtureLeagues:{id}`.
  - Includes we need: `participants`, `scores`, `state`, `statistics`.
  - ([fixtures by date range](https://docs.sportmonks.com/football/endpoints-and-entities/endpoints/fixtures/get-fixtures-by-date-range),
    [leagues](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/leagues-and-seasons/leagues))
- **Time.** `starting_at` is `YYYY-MM-DD HH:MM:SS`, UTC unless a `timezone` parameter is passed;
  `starting_at_timestamp` is unix. We use the unix value.
  ([fixture](https://docs.sportmonks.com/v3/endpoints-and-entities/entities/fixture))
- **Scores** ([scores](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/includes/scores)):

  | Description | Meaning | Our field |
  |---|---|---|
  | `1ST_HALF` | Half-time score | HT |
  | `2ND_HALF` | Cumulative score after 90 minutes | FT |
  | `CURRENT` | Includes extra time | — |

  Each entry also carries `score.participant` (`home`/`away`).
- **Statistics type ids** ([types](https://docs.sportmonks.com/v3/definitions/types/statistics)):

  | Id | Name |
  |---|---|
  | 42 | `SHOTS_TOTAL` |
  | 86 | `SHOTS_ON_TARGET` |
  | 34 | `CORNERS` |
  | 45 | `BALL_POSSESSION` |
  | 5304 | `EXPECTED_GOALS` |

  A type can be absent from a response; absence is not zero.

*Coverage gaps*
- **xG** exists only from the **2024** season. It is a paid add-on, and we do **not** buy it now:
  mixing it with shot-based approximate xG for older seasons is a distribution-shift risk. See
  HANDOFF §9l. ([xG](https://www.sportmonks.com/football-api/xg-data/))
- **Odds history** lasts only ≈ 7 days after kickoff, so Sportmonks cannot backfill odds.
  ([historical odds](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/premium-odds-feed/premium-pre-match-odds/get-all-historical-odds))

**Checked with real calls on 2026-10-05** — answers, evidence and what is still open:
[`provider-evaluation-2026-10-05.md`](./provider-evaluation-2026-10-05.md) (trial history starts at
2024/25; xG and premium odds are refused; includes do not count against other entities' limits).
The original list:

**To confirm with a real call** (during the trial):
- the season name format (mapped to our canonical `YYYY-YYYY`) and the league ids;
- `participants[].meta.location`;
- the state ids and names (FT 5, AET 7, FT_PEN 8, POSTP 10, CANCL 12, ABAN 15 — from the docs
  Q&A, not a reference page);
- whether includes count against the rate limit;
- what Growth includes (one page lists xG in every plan, another sells it as an add-on);
- RPL coverage.

---

## 4. The Odds API — production odds (role: `odds`)

**Status.** Chosen; not implemented yet (provider PRs 4–5). Its ToS explicitly allow commercial
use, indefinite storage, ML training and derived data, so the adapter may declare
`licensed_for_production = True`.

**Plans.** ([plans](https://the-odds-api.com/#get-access), read 2026-10-03)

| Plan | Price / month | Credits / month |
|---|---|---|
| Free | $0 | 500 |
| 20K | $30 | 20,000 |
| 100K | $59 | 100,000 |
| 5M | $119 | 5,000,000 |
| 15M | $249 | 15,000,000 |

**API facts we rely on** (v4; [guide](https://the-odds-api.com/liveapi/guides/v4/))

*Access and quota*
- **Host and auth.** Host `https://api.the-odds-api.com`; the key is the `apiKey` **query
  parameter** — there is no header option. It is therefore scrubbed from every logged URL and
  exception by the shared outbound client (`app/core/outbound.py`: `outbound_client`,
  `check_status`, and the log-record safety net; see HANDOFF "Outbound scrubber").
- **Quota headers** on every response: `x-requests-remaining`, `x-requests-used`,
  `x-requests-last`. A 429 means rate limited. An empty result costs nothing.
- **Time:** ISO 8601 UTC (`Z`).
- **Events:** `id`, `commence_time`, `home_team` / `away_team` as **strings**, no team ids.

*Leagues, bookmakers and markets*
- **Sport keys:** `soccer_epl`, `soccer_spain_la_liga`, `soccer_italy_serie_a`,
  `soccer_germany_bundesliga`, `soccer_france_ligue_one`; `soccer_russia_premier_league` is listed
  too (coverage unverified). ([sports](https://the-odds-api.com/sports-odds-data/sports-apis.html))
- **Pinnacle:** key `pinnacle`, region `eu` only, with the note "odds are from public website which
  may incur a delay". Up to 10 bookmakers count as 1 region.
  ([bookmakers](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html))
- **Markets:** `h2h` includes the draw for soccer; `totals` outcomes carry a `point`. Spreads and
  totals are "mainly available for US sports and bookmakers", so expect sparse totals in `eu`.
  Exchanges add `h2h_lay`. ([markets](https://the-odds-api.com/sports-odds-data/betting-markets.html))

*Endpoints and cost*

| Endpoint | Cost (credits) |
|---|---|
| `/v4/sports`, `/v4/sports/{sport}/events` | 0 |
| `/v4/sports/{sport}/odds` | markets × regions |
| `/v4/historical/sports/{sport}/events` | 1 (0 when empty) |
| `/v4/historical/sports/{sport}/odds?date=` | **10 × markets × regions** |

The historical odds endpoint returns the closest snapshot **at or before** `date`, with `timestamp`,
`previous_timestamp` and `next_timestamp`.
- History starts **2020-06-06**: 10-minute snapshots, 5-minute from 2022-09-18. Consequence: the
  2019-20 matches before the restart have no closing odds in production.

**Closing quote rules** (provider PR 4)
- **Requests.** One historical request per (league, distinct kickoff instant), at
  `date = min(stored kickoff, commence_time)`. A snapshot therefore never lies after the kickoff,
  and the HI-3 time rule holds.
- **Stored rows.** Every `eu` bookmaker's quote in the snapshot is stored with `ts` = the snapshot
  `timestamp`. A quote is `is_closing = true` only when:
  - the snapshot is at most 15 minutes before kickoff; and
  - the market's `last_update` is recent (threshold tuned in the trial).
  Stale quotes are stored as pre-closing.
- **`market_avg` definition.**
  - Input: decimal prices from the same closing snapshot, `eu` region.
  - Excluded: exchanges (`betfair_ex_*`, `matchbook`).
  - Outlier guard: quotes far from the per-outcome median are dropped.
  - Minimum: at least 5 bookmakers per outcome.
  - The price is the mean of the remaining quotes, stored as bookmaker `market_avg` together with
    its bookmaker count.
  - This is our own definition, not football-data's `AvgC*`.
- **Budget.** A `--dry-run` prints the request count and credits before anything is spent. A
  per-run credit cap is checked against `x-requests-remaining`. A ledger of paid snapshots stops a
  re-run from paying twice.
- **Estimate.** h2h only, ≈ 1,000–1,300 kickoff instants per season for 5 leagues gives about
  65–80k credits for 2019-20..2025-26. Totals double that; adding the `uk` region doubles it again.

**Checked with real calls on 2026-10-05** (demo key) — see
[`provider-evaluation-2026-10-05.md`](./provider-evaluation-2026-10-05.md): historical endpoints are
refused with `401 HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN` at 0 credits; error bodies are
`{message, error_code, details_url}`; RPL `eu` h2h has 3–4 bookmakers. The original list:

**To confirm with a real call:**
- the error body format (401/422/429);
- whether historical endpoints work on the free plan;
- the cost of historical event-odds (the docs are ambiguous);
- whether `/odds` can return team ids;
- totals and RPL coverage in `eu`.

---

## 5. Fallback candidates and optional sources (not implemented)

| Source | Role | Note |
|---|---|---|
| API-Football | live (implemented, §2; dev only so far, production has never been launched); fixtures/results as a future fallback | The live poll is implemented; only a fixtures/results fallback adapter is unimplemented. Its ToS say betting-related use may need extra licences from rights holders and say nothing on storage/ML. The poll gets an explicit licence flag, kept **enabled** by owner decision until the written answer arrives; the answer must cover publishing live data and derived predictions |
| TheStatsAPI | fixtures, stats | Fallback only on paper. Its ToS forbid storing data beyond what is reasonably necessary and end the right to use data on termination, so it is unusable for training/history without written confirmation |
| StatsBomb open data | `xg` | Free shot coordinates for a few competitions (xG model training) |
| Sportradar | `live`, `odds` | Officially licensed feeds if ever needed |

---

## Adding a new provider

> **Historical sources** implement the `SourceAdapter` protocol
> (`backend/app/services/ingestion/core.py`): `name`, `may_seed_canonical`, an async
> `fetch(league, season) -> list[FixtureDTO]` where every record carries the source's own
> `external_id`, a tz-aware UTC `kickoff_at` and `kickoff_time_known` — and an **explicit**
> `licensed_for_production = True` only once its Terms of Service allow our use. Without that flag
> the core refuses the adapter in production (fail closed). Identity, season labels and cross-source
> dedup are handled by the core; no schema change is needed.

1. Implement `BaseProvider` in `backend/app/providers/<name>.py`:

```python
from app.providers.base import BaseProvider, Capability

class MyProvider(BaseProvider):
    name = "my_provider"
    capabilities = {Capability.LIVE, Capability.ODDS}

    async def fetch_fixtures(self, date_range) -> list[FixtureDTO]: ...
    async def fetch_live(self) -> list[LiveFixtureDTO]: ...
    async def fetch_odds(self, fixture_id) -> OddsDTO: ...
    async def fetch_stats(self, fixture_id) -> StatsDTO: ...
    async def rate_limit_state(self) -> QuotaDTO: ...
```

2. Register it in `app/providers/registry.py`.
3. Add ID-mapping rules (`provider_team_aliases`, `provider_league_aliases`) — never match on raw
   team names at query time.
4. Write a **contract test** with recorded HTTP fixtures (`tests/providers/test_my_provider.py`):
   the DTOs it returns must satisfy the same schema as every other provider.
5. Add a section to this file. **CI fails if an implemented provider has no section here.**
6. Restart, then add the key in Admin → Providers.

---

## Checking what is loaded

`make data-report` prints, per league and season, fixtures vs expected, teams, score and
kickoff-time coverage, closing 1X2 coverage per bookmaker and O/U 2.5 coverage, and lists
data-quality issues (duplicates across sources, missing/impossible scores, unusable or mislabelled
odds). It only reads, so it is safe on production; `--json` gives machine-readable output and
`--strict` makes warnings fail too. `make verify-history` remains the narrower "every configured
league/season has fixtures" gate.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `429` from a provider | Daily/minute quota hit — check Admin → Providers → quota panel |
| Empty response arrays | League/season id mismatch — re-check against the leagues endpoint |
| Team appears twice in the UI | Missing alias in `provider_team_aliases` — see the ingestion warning log |
| xG missing for a league | Expected: provider xG is patchy. Own-xG requires shot coordinates |
| Predictions stale, "data delayed" badge | Provider quota exhausted or provider down; check ingestion job log |
