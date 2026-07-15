import type { MatchDetail, MatchSummary } from "@/types/match";

export const summaryFixture: MatchSummary = {
  id: "11111111-1111-1111-1111-111111111111",
  league: { code: "EPL", name: "Premier League" },
  home_team: "Arsenal",
  away_team: "Chelsea",
  kickoff_at: "2026-07-15T18:00:00+00:00",
  status: "scheduled",
  minute: null,
  home_score: null,
  away_score: null,
  consensus: { home: 0.52, draw: 0.28, away: 0.2 },
  champion_method: "lightgbm",
  champion_accuracy_pct: 60,
  last_polled_at: null,
  data_delayed: false,
};

export const detailFixture: MatchDetail = {
  ...summaryFixture,
  methods: [
    { method: "lightgbm", is_champion: true, accuracy_pct: 60, probs: { home: 0.49, draw: 0.31, away: 0.2 } },
    { method: "elo", is_champion: false, accuracy_pct: 40, probs: { home: 0.5, draw: 0.3, away: 0.2 } },
    { method: "dixon_coles", is_champion: false, accuracy_pct: 42, probs: { home: 0.48, draw: 0.3, away: 0.22 } },
  ],
  market: { home: 0.4, draw: 0.3, away: 0.3 },
  model_agreement_pct: 97.5,
  delta_vs_market: 0.12,
  tier_required: "pro",
  flags: { methods: "all", per_half_totals: true, live_recompute: true },
};
