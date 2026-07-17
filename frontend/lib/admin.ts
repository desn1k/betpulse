// Browser-side fetchers for the admin dashboard (Phase 12a). All calls go through
// the same-origin /api/admin/* proxy routes, which attach the bearer token; the
// backend enforces admin RBAC.

import { ApiError } from "@/lib/api";
import { authHeader } from "@/lib/auth/store";
import type {
  AdminUserList,
  DisableResult,
  IngestionRuns,
  LlmConfig,
  LlmConfigUpdate,
  ModelsResponse,
  PromoBatch,
  PromoBatchCreated,
  PromoBatchInput,
  PromoteResult,
  Provider,
  ProviderInput,
  Redemption,
  RollbackDiff,
  Snapshot,
  SpendReport,
  Tier,
  TierUpdate,
  UserMutationResult,
  WeightingMode,
} from "@/types/admin";

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { accept: "application/json", ...(init.headers ?? {}), ...authHeader() },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(`request failed: ${res.status}`, res.status, body);
  }
  return (res.status === 204 ? (undefined as T) : ((await res.json()) as T));
}

function jsonBody(data: unknown): RequestInit {
  return { headers: { "content-type": "application/json" }, body: JSON.stringify(data) };
}

// --- providers --------------------------------------------------------------

export function fetchProviders(): Promise<Provider[]> {
  return request<Provider[]>("/api/admin/providers");
}

export function createProvider(input: ProviderInput): Promise<Provider> {
  return request<Provider>("/api/admin/providers", { method: "POST", ...jsonBody(input) });
}

export function updateProvider(id: string, input: Partial<ProviderInput>): Promise<Provider> {
  return request<Provider>(`/api/admin/providers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    ...jsonBody(input),
  });
}

export function deleteProvider(id: string): Promise<void> {
  return request(`/api/admin/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function setProviderEnabled(id: string, enabled: boolean): Promise<Provider> {
  const action = enabled ? "enable" : "disable";
  return request<Provider>(`/api/admin/providers/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
  });
}

// --- ingestion --------------------------------------------------------------

export function fetchIngestionRuns(status?: string): Promise<IngestionRuns> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<IngestionRuns>(`/api/admin/ingestion/runs${query}`);
}

export function rescan(leagues: string[], seasons: string[]): Promise<void> {
  return request("/api/admin/ingestion/rescan", { method: "POST", ...jsonBody({ leagues, seasons }) });
}

// --- models -----------------------------------------------------------------

export function fetchModels(): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/admin/models");
}

export function patchModel(
  id: string,
  changes: { is_enabled?: boolean; is_visible?: boolean; notes?: string },
): Promise<unknown> {
  return request(`/api/admin/models/${encodeURIComponent(id)}`, {
    method: "PATCH",
    ...jsonBody(changes),
  });
}

export function setWeightingMode(mode: WeightingMode): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/admin/models/weighting", {
    method: "PUT",
    ...jsonBody({ mode }),
  });
}

export function setWeights(weights: Record<string, number>): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/admin/models/weights", {
    method: "PUT",
    ...jsonBody({ weights }),
  });
}

export function promoteModel(id: string): Promise<PromoteResult> {
  return request<PromoteResult>(`/api/admin/models/${encodeURIComponent(id)}/promote`, {
    method: "POST",
  });
}

export function demoteModel(id: string): Promise<void> {
  return request(`/api/admin/models/${encodeURIComponent(id)}/demote`, { method: "POST" });
}

export function fetchSnapshots(): Promise<Snapshot[]> {
  return request<Snapshot[]>("/api/admin/models/snapshots");
}

export function fetchSnapshotDiff(id: string): Promise<RollbackDiff> {
  return request<RollbackDiff>(`/api/admin/models/snapshots/${encodeURIComponent(id)}/diff`);
}

export function rollbackSnapshot(id: string): Promise<void> {
  return request(`/api/admin/models/rollback/${encodeURIComponent(id)}`, { method: "POST" });
}

export function retrainModels(): Promise<void> {
  return request("/api/admin/models/retrain", { method: "POST" });
}

// --- LLM spend + config -----------------------------------------------------

export function fetchSpend(days: number): Promise<SpendReport> {
  return request<SpendReport>(`/api/admin/llm/spend?days=${days}`);
}

export function fetchLlmConfig(): Promise<LlmConfig> {
  return request<LlmConfig>("/api/admin/llm-config");
}

export function updateLlmConfig(changes: LlmConfigUpdate): Promise<LlmConfig> {
  return request<LlmConfig>("/api/admin/llm-config", { method: "PATCH", ...jsonBody(changes) });
}

// --- users ------------------------------------------------------------------

export function fetchUsers(params: {
  email?: string;
  tier?: string;
  page?: number;
}): Promise<AdminUserList> {
  const q = new URLSearchParams();
  if (params.email) q.set("email", params.email);
  if (params.tier) q.set("tier", params.tier);
  if (params.page) q.set("page", String(params.page));
  const query = q.toString();
  return request<AdminUserList>(`/api/admin/users${query ? `?${query}` : ""}`);
}

export function assignTier(
  id: string,
  body: { tier_id: string; expires_at?: string | null },
): Promise<UserMutationResult> {
  return request<UserMutationResult>(`/api/admin/users/${encodeURIComponent(id)}/tier`, {
    method: "POST",
    ...jsonBody(body),
  });
}

export function fetchRedemptions(id: string): Promise<Redemption[]> {
  return request<Redemption[]>(`/api/admin/users/${encodeURIComponent(id)}/redemptions`);
}

export function setUserActive(id: string, active: boolean): Promise<DisableResult> {
  const action = active ? "enable" : "disable";
  return request<DisableResult>(`/api/admin/users/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
  });
}

// --- promo ------------------------------------------------------------------

export function fetchBatches(): Promise<PromoBatch[]> {
  return request<PromoBatch[]>("/api/admin/promo/batches");
}

export function createBatch(input: PromoBatchInput): Promise<PromoBatchCreated> {
  return request<PromoBatchCreated>("/api/admin/promo/batches", {
    method: "POST",
    ...jsonBody(input),
  });
}

export function killBatch(id: string): Promise<unknown> {
  return request(`/api/admin/promo/batches/${encodeURIComponent(id)}/kill`, { method: "POST" });
}

// --- tiers ------------------------------------------------------------------

export function fetchTiers(): Promise<Tier[]> {
  return request<Tier[]>("/api/admin/tiers");
}

export function updateTier(id: string, changes: TierUpdate): Promise<Tier> {
  return request<Tier>(`/api/admin/tiers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    ...jsonBody(changes),
  });
}
