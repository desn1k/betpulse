// Mirrors app/schemas/backtester.py.

export type BetType = "1x2" | "total";

export interface StrategyFilter {
  league?: string;
  season?: string;
  odds_min?: number;
  odds_max?: number;
  elo_diff_min?: number;
  elo_diff_max?: number;
  avg_total_min?: number;
  avg_total_max?: number;
  rest_days_min?: number;
}

export interface RunRequest {
  bet_type: BetType;
  pick: string;
  filters: StrategyFilter;
}

export interface WilsonInterval {
  lower: number;
  upper: number;
  confidence: number;
}

export interface Breakdown {
  key: string;
  matched_count: number;
  roi: number;
}

export interface FoldResult {
  season: string;
  matched_count: number;
  roi: number;
}

export interface BacktestResult {
  bet_type: BetType;
  pick: string;
  matched_count: number;
  win_count: number;
  win_rate: number;
  roi: number;
  total_staked: number;
  total_return: number;
  equity_curve: number[];
  max_drawdown: number;
  win_rate_ci: WilsonInterval;
  by_league: Breakdown[];
  by_season: Breakdown[];
  available_bet_types: BetType[];
  roi_disclaimer: boolean;
  small_sample_warning: boolean;
  walk_forward: boolean;
  out_of_sample_roi: number | null;
  folds: FoldResult[];
}
