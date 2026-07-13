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

**What it is.** Free CSV archives of European league results, going back 30+ seasons. Each row has
full-time and **half-time** scores, shots, corners, cards — and **closing odds from 10+ bookmakers**
(Pinnacle, Bet365, William Hill, …).

**Why it is the backbone.** Without closing odds we cannot compute ROI or CLV, which means the
strategy backtester and the market-implied benchmark would be impossible. This dataset bootstraps
the whole ML layer for free.

**Access.** No registration, no API key. Plain CSV over HTTPS.

**Coverage.** Major European divisions (England, Spain, Italy, Germany, France + second tiers and
several others). **Does not cover RPL** — see the caveat below.

**How to load.**

```bash
make bootstrap-history          # downloads + normalizes + loads all configured seasons
make bootstrap-history SEASONS=2015-2026 LEAGUES=EPL,LALIGA,SERIEA,BUNDESLIGA,LIGUE1
```

Expect a few hundred MB of raw CSV and a few minutes of processing. Verify with:

```bash
make verify-history             # prints row counts per league/season; fails on gaps
```

**Caveats.**
- Column names drift across seasons — the loader normalizes them; keep the mapping table updated.
- No shot coordinates → **no own-xG for historical seasons** from this source. Historical xG features
  are approximated from shots/shots-on-target until a shot-level source is connected.
- Team names differ from API-Football → handled by the **ID-mapping layer**
  (`provider_team_aliases`). Unmapped names raise an ingestion warning, never a silent mismatch.

**Legal.** Free for personal/non-commercial analysis; check the site's terms before commercial use
and attribute the source.

---

## 2. API-Football (api-sports.io) — live (roles: `live`, `odds`, optionally `xg`)

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
