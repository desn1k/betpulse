import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { fetchAnalysis, fetchMatch, fetchMatches } from "./api";
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

export function useMatch(id: string): UseQueryResult<MatchDetail> {
  return useQuery({
    queryKey: matchKeys.detail(id),
    queryFn: () => fetchMatch(id),
    refetchInterval: 60_000,
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
