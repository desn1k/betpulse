// Frontend contract for the public match endpoints. Mirrors the backend Pydantic
// schemas in app/schemas/matches.py — keep the two in sync.

export type FixtureStatus = "scheduled" | "live" | "finished";

export interface LeagueRef {
  code: string;
  name: string;
}

export interface Probs1x2 {
  home: number;
  draw: number;
  away: number;
}

export interface MethodPrediction {
  method: string;
  is_champion: boolean;
  accuracy_pct: number | null;
  probs: Probs1x2;
}

export interface MatchSummary {
  id: string;
  league: LeagueRef;
  home_team: string;
  away_team: string;
  kickoff_at: string;
  status: FixtureStatus;
  minute: number | null;
  home_score: number | null;
  away_score: number | null;
  consensus: Probs1x2 | null;
  champion_method: string | null;
  champion_accuracy_pct: number | null;
  last_polled_at: string | null;
  data_delayed: boolean;
}

export interface MatchList {
  items: MatchSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface MatchDetail extends MatchSummary {
  methods: MethodPrediction[];
  market: Probs1x2 | null;
  model_agreement_pct: number | null;
  delta_vs_market: number | null;
  tier_required: string;
}

export interface MatchListParams {
  league?: string;
  status?: FixtureStatus;
  date?: string;
  limit?: number;
  offset?: number;
}
