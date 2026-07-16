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
