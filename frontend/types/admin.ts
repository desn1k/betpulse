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
  enabled_from: boolean;
  enabled_to: boolean;
  visible_from: boolean;
  visible_to: boolean;
}

export interface RollbackDiff {
  changes: RollbackChange[];
}


// --- system health + audit (Phase 12d) --------------------------------------

export type HealthStatus = "ok" | "degraded" | "error" | "not_configured";

export interface ComponentHealth {
  name: string;
  status: HealthStatus;
  detail: string | null;
  latency_ms: number | null;
  meta: Record<string, unknown>;
}

export interface SystemHealth {
  status: "ok" | "degraded" | "error";
  checked_at: string;
  components: ComponentHealth[];
}

export interface OpsAlertResult {
  status: "sent" | "not_configured";
  detail: string | null;
}

export interface AuditEvent {
  id: string;
  actor_user_id: string | null;
  actor_email: string | null;
  action: string;
  target: string | null;
  ip: string | null;
  user_agent: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface AuditLogList {
  events: AuditEvent[];
  total: number;
  page: number;
  per_page: number;
}

// --- LLM spend (Phase 12c) ---------------------------------------------------

export interface DailySpend {
  day: string; // UTC calendar day, ISO date
  tokens_in: number;
  tokens_out: number;
  cost: string;
  count: number;
}

export interface FixtureSpend {
  fixture_id: string;
  home: string;
  away: string;
  league: string;
  cost: string;
  tokens_in: number;
  tokens_out: number;
  count: number;
}

export interface SpendReport {
  days: number;
  since: string;
  daily: DailySpend[];
  top_fixtures: FixtureSpend[];
  daily_token_budget: number;
  total_cost: string;
  total_tokens: number;
}

export interface LlmConfig {
  base_url: string;
  model: string;
  key_masked: string | null;
  max_tokens: number;
  daily_token_budget: number;
  cache_ttl_seconds: number;
  cost_per_1k_in: string;
  cost_per_1k_out: string;
  is_enabled: boolean;
}

export interface LlmConfigUpdate {
  base_url?: string;
  model?: string;
  api_key?: string;
  max_tokens?: number;
  daily_token_budget?: number;
  cache_ttl_seconds?: number;
  cost_per_1k_in?: string;
  cost_per_1k_out?: string;
  is_enabled?: boolean;
}

// --- user management (Phase 12c) --------------------------------------------

export type UserTier = "free" | "pro" | "expert";

export interface AdminUser {
  id: string;
  email: string;
  role: "user" | "admin";
  base_tier: UserTier;
  effective_tier: string;
  tier_expires_at: string | null;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
}

export interface AdminUserList {
  users: AdminUser[];
  total: number;
  page: number;
  per_page: number;
}

export interface UserMutationResult {
  id: string;
  is_active: boolean;
  effective_tier: string;
  tier_expires_at: string | null;
}

export interface DisableResult {
  id: string;
  is_active: boolean;
  revoked_tokens: number;
}

export type PromoCodeType = "percent" | "fixed" | "trial" | "upgrade";
export type PromoRedemptionStatus = "applied" | "pending" | "expired";

export interface Redemption {
  id: string;
  batch_id: string;
  code_type: PromoCodeType;
  value: string | null;
  status: PromoRedemptionStatus;
  redeemed_at: string;
}

// --- promo (Phase 12c) ------------------------------------------------------

export type PromoBatchStatus = "active" | "disabled";

export interface PromoBatch {
  id: string;
  name: string;
  code_type: PromoCodeType;
  value: string | null;
  tier_id: string | null;
  bound_user_id: string | null;
  max_activations: number;
  size: number;
  stackable: boolean;
  expires_at: string | null;
  status: PromoBatchStatus;
  created_at: string;
}

export interface PromoBatchInput {
  name: string;
  code_type: PromoCodeType;
  size: number;
  value?: string | null;
  tier_id?: string | null;
  bound_user_id?: string | null;
  max_activations: number;
  expires_at?: string | null;
  stackable: boolean;
}

export interface PromoBatchCreated {
  batch: PromoBatch;
  codes: string[];
  warning: string;
}

// --- tiers (Phase 12c) ------------------------------------------------------

export interface Tier {
  id: string;
  name: string;
  price: string;
  period: string | null;
  feature_flags: Record<string, unknown>;
  limits: Record<string, unknown>;
  is_public: boolean;
  sort_order: number;
}

export interface TierUpdate {
  price?: string;
  period?: string | null;
  feature_flags?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  is_public?: boolean;
}
