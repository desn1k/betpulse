// Browser-side fetchers for the admin dashboard (Phase 12a). All calls go through
// the same-origin /api/admin/* proxy routes, which attach the bearer token; the
// backend enforces admin RBAC.

import { ApiError } from "@/lib/api";
import { authHeader } from "@/lib/auth/store";
import type { IngestionRuns, Provider, ProviderInput } from "@/types/admin";

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
