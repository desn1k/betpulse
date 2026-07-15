// Browser-side fetchers for the same-origin match proxy routes. Consumed by the
// TanStack Query hooks in lib/queries.ts.

import { authHeader } from "@/lib/auth/store";
import type { BacktestResult, RunRequest } from "@/types/backtester";
import type { AnalysisResult } from "@/types/llm";
import type { MatchDetail, MatchList, MatchListParams } from "@/types/match";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(url: string): Promise<T> {
  // Attach the bearer token (when signed in) so the backend resolves the tier.
  const res = await fetch(url, { headers: { accept: "application/json", ...authHeader() } });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(`request failed: ${res.status}`, res.status, body);
  }
  return (await res.json()) as T;
}

export function buildMatchesQuery(params: MatchListParams): string {
  const search = new URLSearchParams();
  if (params.league) search.set("league", params.league);
  if (params.status) search.set("status", params.status);
  if (params.date) search.set("date", params.date);
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  const query = search.toString();
  return `/api/matches${query ? `?${query}` : ""}`;
}

export function fetchMatches(params: MatchListParams): Promise<MatchList> {
  return getJson<MatchList>(buildMatchesQuery(params));
}

export function fetchMatch(id: string): Promise<MatchDetail> {
  return getJson<MatchDetail>(`/api/matches/${encodeURIComponent(id)}`);
}

export function fetchAnalysis(id: string, language: string): Promise<AnalysisResult> {
  const query = new URLSearchParams({ language }).toString();
  return getJson<AnalysisResult>(`/api/matches/${encodeURIComponent(id)}/analysis?${query}`);
}

export interface RedeemEffect {
  type: "percent" | "fixed" | "trial" | "upgrade";
  value: string | null;
  status: "applied" | "pending" | "expired";
}

export async function redeemPromo(code: string): Promise<RedeemEffect> {
  const res = await fetch("/api/promo/redeem", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json", ...authHeader() },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(`redeem failed: ${res.status}`, res.status, body);
  }
  return ((await res.json()) as { effect: RedeemEffect }).effect;
}

export async function runBacktest(
  request: RunRequest,
  walkForward = false,
): Promise<BacktestResult> {
  const url = `/api/backtester/run${walkForward ? "?walk_forward=true" : ""}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json", ...authHeader() },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(`backtest failed: ${res.status}`, res.status, body);
  }
  return (await res.json()) as BacktestResult;
}

export { ApiError };
