// Browser-side fetchers for the same-origin match proxy routes. Consumed by the
// TanStack Query hooks in lib/queries.ts.

import type { MatchDetail, MatchList, MatchListParams } from "@/types/match";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { accept: "application/json" } });
  if (!res.ok) {
    throw new ApiError(`request failed: ${res.status}`, res.status);
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

export { ApiError };
