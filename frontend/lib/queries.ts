import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { ApiError, fetchAnalysis, fetchMatch, fetchMatches } from "./api";
import type { AnalysisResult } from "@/types/llm";
import type { MatchDetail, MatchList, MatchListParams } from "@/types/match";

export const matchKeys = {
  all: ["matches"] as const,
  list: (params: MatchListParams) => ["matches", "list", params] as const,
  detail: (id: string) => ["matches", "detail", id] as const,
  analysis: (id: string, language: string) => ["matches", "analysis", id, language] as const,
};

export function useMatches(params: MatchListParams): UseQueryResult<MatchList> {
  return useQuery({
    queryKey: matchKeys.list(params),
    queryFn: () => fetchMatches(params),
    // Live scores move; keep the list reasonably fresh without hammering.
    refetchInterval: 60_000,
  });
}

export const MATCH_REFETCH_MS = 60_000;
const DAY_MS = 24 * 60 * 60 * 1000;
const MIDNIGHT_JITTER_MS = 60_000;

/**
 * When to poll a match card again, given the last fetch's error (F8).
 *
 * - 404: the match is gone; stop (a focus refetch may still try once).
 * - 403: the daily view quota is spent; the next day's quota starts at UTC
 *   midnight, so try then, spread over a minute (``errorUpdatedAt`` gives each
 *   tab a stable offset) instead of every 60 s.
 * - Anything else, or no error: the usual 60 s.
 */
export function matchRefetchInterval(
  error: unknown,
  errorUpdatedAt: number,
  now: number = Date.now(),
): number | false {
  if (error instanceof ApiError && error.status === 404) return false;
  if (error instanceof ApiError && error.status === 403) {
    const untilMidnight = DAY_MS - (now % DAY_MS);
    return untilMidnight + (errorUpdatedAt % MIDNIGHT_JITTER_MS);
  }
  return MATCH_REFETCH_MS;
}

export function useMatch(id: string): UseQueryResult<MatchDetail> {
  return useQuery({
    queryKey: matchKeys.detail(id),
    queryFn: () => fetchMatch(id),
    refetchInterval: (query) =>
      matchRefetchInterval(query.state.error, query.state.errorUpdatedAt),
    // Off app-wide; on here so a tab left on a 403 or 404 recovers when the
    // user comes back to it.
    refetchOnWindowFocus: true,
  });
}

export function useAnalysis(id: string, language: string): UseQueryResult<AnalysisResult> {
  return useQuery({
    queryKey: matchKeys.analysis(id, language),
    queryFn: () => fetchAnalysis(id, language),
    // The analysis is cached server-side per (fixture, model); it barely changes,
    // so don't poll and keep it fresh for the session.
    staleTime: 5 * 60_000,
    retry: false,
  });
}
