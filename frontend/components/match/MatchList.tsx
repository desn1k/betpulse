"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { useMatches } from "@/lib/queries";
import type { FixtureStatus } from "@/types/match";

import { MatchCard } from "./MatchCard";
import { MatchFilters } from "./MatchFilters";
import { MatchListSkeleton } from "./MatchCardSkeleton";

/** The match feed: filter bar + responsive grid of cards, with loading/empty states. */
export function MatchList() {
  const t = useTranslations();
  const [league, setLeague] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<FixtureStatus | undefined>(undefined);

  const query = useMatches({ league, status, limit: 30 });

  const remaining = query.data?.matches_remaining ?? null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <MatchFilters
          league={league}
          status={status}
          onLeagueChange={setLeague}
          onStatusChange={setStatus}
        />
        {remaining !== null && (
          <Badge variant="neutral" aria-live="polite">
            {t("list.matchesLeft", { count: remaining })}
          </Badge>
        )}
      </div>

      {query.isPending ? (
        <MatchListSkeleton />
      ) : query.isError ? (
        <p role="alert" className="rounded-card bg-surface-muted p-6 text-center text-muted-strong">
          {t("list.error")}
        </p>
      ) : query.data.items.length === 0 ? (
        <p className="rounded-card bg-surface-muted p-6 text-center text-muted-strong">
          {t("list.empty")}
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {query.data.items.map((match) => (
            <MatchCard key={match.id} match={match} />
          ))}
        </div>
      )}
    </div>
  );
}
