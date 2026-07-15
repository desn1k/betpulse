# Data Sources

How this project gets football data: what each source is for, how to obtain access, where to put the
key, and what the request budget looks like.

> **Rule:** provider API keys are entered in the **Admin UI** (Admin → Providers → Add provider).
> They are envelope-encrypted at rest and never returned to the browser — only a masked suffix is
> shown. The keys in `.env` exist **only** as a local-dev / CI fallback.

---

## The two data planes

We deliberately use **two different sources**, because no single one does both jobs well.

| Plane | Purpose | Source |
|---|---|---|
| **Historical** | ML training, calibration, strategy backtester, ROI/CLV | football-data.co.uk (free CSV) |
| **Live / upcoming** | Fixtures, in-play state, odds, stats | API-Football (api-sports.io) |

Roles are assigned per provider in the admin UI: `historical`, `live`, `odds`, `xg`. A provider can
hold several roles. Ingestion picks the **highest-priority provider that has the required role and
remaining quota**.

---

## 1. football-data.co.uk — historical (role: `historical`, `odds`)

**Provider id:** `football_data_couk` (implemented in `backend/app/providers/football_data_couk.py`).

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

**Column mapping (as implemented).** The loader (pandas) reads these columns; season-format drift is
handled by fallbacks:

| Field | CSV column(s) |
|---|---|
| kickoff date / time | `Date` (dayfirst), optional `Time` |
| full-time goals | `FTHG`, `FTAG` |
| half-time goals | `HTHG`, `HTAG` |
| shots / on target | `HS`,`AS` / `HST`,`AST` |
| corners | `HC`, `AC` |
| **Pinnacle closing 1X2** | `PSCH`,`PSCD`,`PSCA` → fallback `PSH`,`PSD`,`PSA` |

Closing odds are stored in the `odds` hypertable as `bookmaker='pinnacle'`, `market='1x2'`,
`outcome in {home,draw,away}`, `ts = kickoff`, `is_closing = true`.

**ID mapping (seed behaviour).** football-data.co.uk is the **canonical seed source**: the first time
a team/league name is seen it creates the canonical `teams`/`leagues` row (keyed by normalized name /
league code) and records the alias in `provider_team_aliases` / `provider_league_aliases`. Each
creation emits a structured-JSON `WARNING` with `league`, `raw_name`, `normalized` and `csv_row` so it
is auditable — never a silent duplicate. **Other** providers resolve against these aliases via a
strict resolver that **raises** `UnmappedEntityError` on an unknown name.

**How to load.**

```bash
make bootstrap-history HISTORY_ARGS="--leagues EPL,LALIGA --seasons 2022-2023,2023-2024"
make verify-history    HISTORY_ARGS="--leagues EPL,LALIGA --seasons 2022-2023,2023-2024"
```

`bootstrap-history` downloads from football-data.co.uk (local dev / VPS). `verify-history` prints a
`league | season | fixtures | odds` table and exits non-zero if any configured league/season has zero
fixtures. Ingestion is **idempotent** — re-running the same CSV inserts nothing new
(`ON CONFLICT DO NOTHING` on the fixture and odds identity keys).

**Caveats.**
- Kickoff times are stored as UTC from the CSV `Date`/`Time` (football-data times are UK local; the
  small offset is immaterial for closing-odds analysis).

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

## 3. Optional future sources

| Source | Role | Why you might add it |
|---|---|---|
| Sportmonks | `xg`, `live` | Paid xG and shot-level data on higher tiers |
| The Odds API | `odds` | Multi-bookmaker line comparison / value detection |
| StatsBomb open data | `xg` | Free shot coordinates for a limited set of competitions — good for training the xG model |
| Sportradar | `live`, `odds` | Officially licensed feeds if the product ever needs them |

---

## Adding a new provider

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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `429` from a provider | Daily/minute quota hit — check Admin → Providers → quota panel |
| Empty response arrays | League/season id mismatch — re-check against the leagues endpoint |
| Team appears twice in the UI | Missing alias in `provider_team_aliases` — see the ingestion warning log |
| xG missing for a league | Expected: provider xG is patchy. Own-xG requires shot coordinates |
| Predictions stale, "data delayed" badge | Provider quota exhausted or provider down; check ingestion job log |
