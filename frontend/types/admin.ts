// Frontend contract for the admin endpoints (Phase 12a). Mirrors app/schemas/
// providers.py and ingestion.py.

export type ProviderRole = "historical" | "live" | "odds" | "xg";

export interface Provider {
  id: string;
  name: string;
  roles: string[];
  priority: number;
  key_masked: string | null;
  requests_per_minute: number | null;
  requests_per_day: number | null;
  quota_state: Record<string, unknown>;
  is_enabled: boolean;
}

export interface ProviderInput {
  name: string;
  roles: string[];
  priority?: number;
  api_key?: string;
  requests_per_minute?: number | null;
  requests_per_day?: number | null;
  is_enabled?: boolean;
}

export type IngestionStatus = "running" | "success" | "partial" | "failed";

export interface IngestionRun {
  id: string;
  provider: string;
  league: string | null;
  season: string | null;
  status: IngestionStatus;
  fixtures_ingested: number;
  odds_ingested: number;
  error: string | null;
  triggered_by: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface IngestionRuns {
  runs: IngestionRun[];
  total: number;
  page: number;
  per_page: number;
}

export type ModelStatus = "challenger" | "champion" | "retired";
export type WeightingMode = "auto" | "manual";

export interface Model {
  id: string;
  method: string;
  version: string;
  status: ModelStatus;
  accuracy_pct: number | null;
  brier: number | null;
  log_loss: number | null;
  roi_vs_closing: number | null;
  sample_count: number;
  is_enabled: boolean;
  is_visible: boolean;
  display_weight: number;
  min_samples: number;
  notes: string | null;
  last_trained_at: string | null;
  last_evaluated_at: string | null;
}

export interface ModelsResponse {
  models: Model[];
  weighting_mode: WeightingMode;
}

export interface PromoteResult {
  promoted: boolean;
  warning: string | null;
}

export interface Snapshot {
  id: string;
  reason: string;
  actor: string | null;
  taken_at: string;
}

export interface RollbackChange {
  method: string;
  version: string;
  status_from: string;
  status_to: string;
  weight_from: number;
  weight_to: number;
}

export interface RollbackDiff {
  changes: RollbackChange[];
}
