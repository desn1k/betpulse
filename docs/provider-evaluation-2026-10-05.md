# Provider fit report — 2026-10-05

Does each chosen provider fit BetPulse (HANDOFF §9l)? Every statement below comes from a **real
response** recorded on 2026-10-05 with `python -m app.cli provider-record`, saved under
`backend/tests/fixtures/{the_odds_api,sportmonks}/` (keys replaced by `REDACTED`, account blocks
dropped) and pinned by `backend/tests/providers/test_recorded_contracts.py`. The manifests next to
the fixtures list every call. No secrets are in this file.

| Provider | Account | Calls | Spent |
|---|---|---|---|
| The Odds API | demo key (500 credits/month) | 10 + 1 zero-cost re-record | **3 credits** (497 left) |
| Sportmonks | Growth trial (ends about 2026-10-17), 30 leagues | 3 + 20 + 13 + 11 (stage 2c) (+ 3 zero-cost re-record) | no credits; hourly per-entity limit, lowest `remaining` seen 2478 of 2500 |

## Verdicts

- **The Odds API — fits for the five big leagues; does not fit RPL for `market_avg`.**
  Pinnacle is in every EPL match, h2h and totals are both well covered in `eu`. RPL has only
  3–4 bookmakers per match, below our minimum of 5 for `market_avg`. The historical backfill
  (provider PR 4) cannot be checked on the demo key: history is refused. Buying the 20K plan is
  needed before PR 4, and the coverage rule in HANDOFF §9l (Pinnacle ≈ 90 % after 2025-07-23)
  is still unmeasured.
- **Sportmonks — fits as the fixtures/results/stats source for 2024-25 onward, including the
  current season and upcoming fixtures; history before 2024-25 is unconfirmed, and that decides
  whether it can be the backfill source.**
  All six of our leagues are on the plan; finished 2025-26 and 2024-25 fixtures come back with
  kickoff times, home/away, half-time and full-time scores, 37–43 statistic types per match and
  lineups in **all six leagues** (stage 2c). The current season 2026/27 and the next 7 days of
  scheduled fixtures are served with kickoff times (stage 2c).
  xG is refused on this plan. On the trial, `/seasons` lists only 2024/25–2026/27 for every one
  of our leagues and 2019-20/2015-16 fixture calls are empty, so the 2019-20..2023-24 backfill
  (HANDOFF §9l) is **not possible on what we saw**; whether paid Growth goes deeper needs a
  written answer from Sportmonks.

## The Odds API (demo key)

| Call | Status | Credits | Remaining |
|---|---|---|---|
| `/v4/sports` | 200 | 0 | 500 |
| `/v4/sports/soccer_epl/events` | 200 (20 events, 2026-10-10…10-19) | 0 | 500 |
| `/v4/sports/soccer_russia_premier_league/events` | 200 (8 events, 2026-10-09…10-11) | 0 | 500 |
| `/odds` EPL, `eu`, `h2h` | 200 | 1 | 499 |
| `/odds` EPL, `eu`, `totals` | 200 | 1 | 498 |
| `/odds` RPL, `eu`, `h2h` | 200 | 1 | 497 |
| `/v4/historical/.../odds` EPL (date 2025-08-16) | **401** `HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN` | 0 | 497 |
| `/v4/historical/.../events` EPL | **401**, same code | 0 | 497 |
| `/v4/sports`, wrong key | 401 `INVALID_KEY` | — | — |
| `/odds` with `markets=not_a_market` | 422 `INVALID_MARKET` | 0 | 497 |

- **League coverage.** All six sport keys are active: `soccer_epl`, `soccer_spain_la_liga`,
  `soccer_italy_serie_a`, `soccer_germany_bundesliga`, `soccer_france_ligue_one`,
  `soccer_russia_premier_league`.
- **EPL h2h (`eu`).** 20 matches; 17–23 bookmakers per match (16–21 without the exchanges
  `betfair_ex_eu` and `matchbook`); **Pinnacle in all 20 matches**. Exchanges also return an
  `h2h_lay` market (excluded from `market_avg` by definition).
- **EPL totals (`eu`).** 20 matches, 11–16 bookmakers, Pinnacle in all 20; lines 2.25 to 3.5
  (2.5 present). Better than the docs' "mainly US" warning suggested.
- **RPL h2h (`eu`).** 8 matches, **3–4 bookmakers per match** (betonlineag, codere_it,
  marathonbet, onexbet, pinnacle): **below our minimum of 5 for `market_avg`**. Pinnacle is in
  all 8.
- **Bookmakers seen (`eu`, h2h):** betclic_fr, betfair_ex_eu, betonlineag, betsson, codere_it,
  coolbet, everygame, gtbets, leovegas_se, marathonbet, matchbook, mybookieag, nordicbet,
  onexbet, pinnacle, sport888, tipico_de, unibet_fr, unibet_nl, unibet_se, williamhill,
  winamax_de, winamax_fr.
- **Credits** match the v4 guide: events/sports 0, odds = markets × regions; a refused or
  invalid call costs 0. Every response carries `x-requests-last/used/remaining` (logged per call
  by `app/providers/http.py`); the invalid-key 401 carries none.
- **Error bodies** are JSON `{message, error_code, details_url}`.
- **Identity.** Events have a string `id` and team **names** only (no team ids), as documented.

## Sportmonks (Growth trial)

**Scope.** The trial includes 30 leagues; only our six target leagues were tested. The other 24
are out of scope for this evaluation.

**Leagues on the plan** (`/v3/football/leagues`, 30 in total). Ours, all covered:

| Our league | Sportmonks id | Current season |
|---|---|---|
| EPL | 8 | 2026/2027 |
| La Liga | 564 | 2026/2027 |
| Serie A | 384 | 2026/2027 |
| Bundesliga | 82 | 2026/2027 |
| Ligue 1 | 301 | 2026/2027 |
| RPL | 486 | 2026/2027 |

Others on the trial: Championship, FA Cup, Carabao Cup, Eredivisie, Belgian Pro League, Danish
Superliga, Serie B, Coppa Italia, Liga Portugal, Scottish Premiership, La Liga 2, Copa del Rey,
Allsvenskan, Süper Lig, Brazilian Série A, Saudi Pro League, and play-off stages.

**Answers from the responses**

| Question | Answer | Evidence |
|---|---|---|
| History depth per league | `/seasons` lists **only 2024/2025, 2025/2026, 2026/2027** for each of the six leagues; 2019-20 opening rounds (all six) and EPL 2015-16 return no fixtures | `seasons_*.json`, `fixtures_2019_*.json`, `fixtures_2015_epl.json` |
| Finished fixtures served | Yes: EPL 2025-26 opening (10 fixtures, with and without the league filter), the full 2025/26 season by id (380 fixtures, all FT), EPL 2024-25 opening with all includes | `b_fixtures_2025_*.json`, `b_season_2526_epl_with_fixtures.json`, `b_fixtures_2024_epl_opening_full.json` |
| Kickoff time | Present: `starting_at` `YYYY-MM-DD HH:MM:SS` (UTC) and `starting_at_timestamp` (unix), e.g. 2024-08-16 19:00:00 | same |
| Per-match statistics | Yes, in all six leagues: EPL 2024-25 opening 37–39 types per match (one entry per team); La Liga, Serie A, Bundesliga, Ligue 1 and RPL, three finished 2025-26 matches each (2025-10-17…20), 39–43 types; all include `SHOTS_TOTAL`, `SHOTS_ON_TARGET`, `CORNERS`, `BALL_POSSESSION`; **no `EXPECTED_GOALS`** anywhere | `b_fixtures_2024_epl_opening_full.json`, `c_fixtures_2526_*_full.json` |
| xG on this plan | **No**: `include=xGFixture` → 403, code 5002 "You do not have access to the 'xgfixture' include" | `fixtures_recent_xg_epl.json` |
| Lineups | Yes, in all six leagues: 39–50 entries per match (player, team, position, formation slot, jersey) | `b_fixtures_2024_epl_opening_full.json`, `c_fixtures_2526_*_full.json` |
| `participants[].meta.location` | Yes, all six leagues: `home`/`away`, plus `winner` and table `position` | same |
| Scores | `1ST_HALF` (HT), `2ND_HALF` (FT after 90'), `2ND_HALF_ONLY`, `CURRENT`, each per `participant` home/away | same |
| States | 25 states; FT 5, AET 7, FT_PEN 8, POSTPONED 10, CANCELLED 12, ABANDONED 15, AWARDED 17, DELETED 20 (the ids taken from the docs Q&A are right) | `states.json` |
| Season name format | `2026/2027` (calendar-year leagues: `2026`) | `leagues_p1.json`, `seasons_*.json` |
| Rate-limit reporting | Body block `rate_limit {resets_in_seconds, remaining, requested_entity}`, 2500 per entity per hour; absent on 401/403 | every 200 fixture |
| Do includes count toward the limit? | **No, on what we measured.** Each call takes 1 from its own entity. The Team bucket stayed at 2499→2498 (its own two probes) across fixture calls that included `participants`; the Fixture bucket did not move for a Season call that included 380 fixtures | `teams_probe_after_includes.json`, `b_teams_probe_after_includes.json`, quota fields |
| Premium odds | Refused: `include=premiumOdds` → 403, code 5002 | `error_premium_include.json` |
| Empty vs no access vs missing | All look the same: **200** with `data: []` (or no `data`) and "No result(s) found … or you don't have access to it via your current subscription" — even for a nonexistent fixture id (no 404) | `error_not_found.json`, empty fixture files |
| Live format | **Not observed**: `/livescores` and `/livescores/inplay` returned no fixtures each time (latest 2026-10-05 ~12:25 UTC, a Monday with no match in our leagues) | `livescores_inplay.json`, `b_livescores_today.json`, `c_livescores*.json` |
| Errors | 401 `{"message": "Invalid token provided"}`; 403 `{message, link, code}` | `error_invalid_token.json` |

## Current season and upcoming fixtures (stage 2c)

**Conclusion: yes — Sportmonks serves current-season and upcoming fixtures on this plan, with
kickoff times.** What pre-match predictions need is there.

- **Why the earlier windows were empty.** Season 2026/27 by id (28083, EPL) with
  `include=fixtures` returns 380 fixtures: 50 finished (state 5) and 330 not started (state 1).
  **None is dated 2026-09-21…2026-10-09**: the last played round is 2026-09-20, the next starts
  2026-10-10. The stage 2/2b windows (2026-09-25…10-05 and 2026-10-02…10-05) fell entirely inside
  that gap. It was a gap in the schedule, not a filter problem and not a plan restriction:
  - the same league filter returns all 50 finished EPL fixtures for 2026-08-21…2026-10-05
    (`c_fixtures_2627_epl_filtered.json`);
  - the unfiltered window returns fixtures from 12 leagues, ours included
    (`c_fixtures_2627_unfiltered.json`).
- **Scope of that evidence.** The season-by-id check covers EPL only. For the other five
  leagues it rests on the empty 6-league windows plus the upcoming call, whose first fixture is
  2026-10-09; their season lists were not fetched by id.
- **Upcoming 7 days** (2026-10-05…10-12, six leagues, `participants;state`): the first page holds
  50 fixtures, all `NS`, from 2026-10-09 16:30 to 2026-10-11 18:45 UTC; more pages exist
  (`has_more`). Per league on that page: EPL 9, Bundesliga 9, La Liga 8, Serie A 8, Ligue 1 8,
  RPL 8. Each has `starting_at` (UTC), `starting_at_timestamp` and home/away participants
  (`c_upcoming_7d.json`).
- **Live retry windows** (before the trial ends; each needs the owner's ok, then two calls,
  `/livescores` and `/livescores/inplay`):
  - RPL, Friday 2026-10-09, from about **16:45 UTC** (first kickoff 16:30);
  - EPL, Saturday 2026-10-10, from about **11:45 UTC** (first kickoff 11:30).

## What we cannot conclude yet

- **Sportmonks history on paid Growth.** The trial shows 2024/25 onward only. Whether the paid
  plan serves 2019-20..2023-24 (our backfill range) needs a written answer from Sportmonks.
  Without it, Sportmonks cannot be the source for those seasons.
- **Sportmonks live format.** No live fixture at any call time; retry windows are listed above.
- **The Odds API history:** Pinnacle closing coverage after 2025-07-23 (the §9l decision rule),
  snapshot timestamps, the cost of historical event-odds — all need the paid plan.
- **Licences (written ToS answers, HANDOFF §9l):** Sportmonks on ML training; API-Football on
  publishing live data and derived predictions; storage after the subscription ends.

## Gaps and risks

- RPL odds are too thin for `market_avg` (3–4 bookmakers). Options: Pinnacle-only reference for
  RPL, a lower RPL-specific minimum, or no market comparison for RPL.
- Sportmonks' "empty = no access = missing" answer means an adapter cannot tell a plan gap from a
  real absence by status; it must check coverage explicitly (seasons list, counts per round).
- xG stays shot-based (as decided in §9l); the plan refuses Sportmonks xG anyway.
- If paid Growth history also starts at 2024/25, the 2019-20..2023-24 training seasons need
  another source (API-Football as the fallback in §9l, subject to its ToS answer).
