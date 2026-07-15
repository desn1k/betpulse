import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { fetchMatch, fetchMatches } from "./api";
import type { MatchDetail, MatchList, MatchListParams } from "@/types/match";

export const matchKeys = {
  all: ["matches"] as const,
  list: (params: MatchListParams) => ["matches", "list", params] as const,
  detail: (id: string) => ["matches", "detail", id] as const,
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
