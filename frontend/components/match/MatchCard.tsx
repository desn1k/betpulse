"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import type { MatchSummary } from "@/types/match";

import { MatchStatus } from "./MatchStatus";
import { ProbabilityBar } from "./ProbabilityBar";

// Shared fixed height so the card and its skeleton occupy the exact same box —
// swapping one for the other causes zero layout shift (a hard requirement).
export const MATCH_CARD_HEIGHT = "h-[188px]";

function Score({ value }: { value: number | null }) {
  return <span className="tabular-nums">{value ?? "–"}</span>;
}

export function MatchCard({ match }: { match: MatchSummary }) {
  const t = useTranslations();
  const showScore = match.status !== "scheduled";

  return (
    <Link
      href={`/matches/${match.id}`}
      className="block focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
      aria-label={`${match.home_team} – ${match.away_team}`}
    >
      <Card
        className={cn(
          MATCH_CARD_HEIGHT,
          "flex flex-col justify-between p-4 transition hover:-translate-y-0.5 hover:shadow-lg",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="truncate text-xs font-semibold uppercase tracking-wide text-muted">
            {match.league.code}
          </span>
          <div className="flex flex-shrink-0 items-center gap-1.5">
            {match.data_delayed && <Badge variant="warn">{t("card.dataDelayed")}</Badge>}
            <MatchStatus match={match} />
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 text-base font-semibold text-foreground">
          <span className="truncate">{match.home_team}</span>
          {showScore && (
            <span className="flex-shrink-0 tabular-nums text-muted-strong">
              <Score value={match.home_score} />
              {" : "}
              <Score value={match.away_score} />
            </span>
          )}
          <span className="truncate text-right">{match.away_team}</span>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted">{t("card.consensus")}</span>
          {match.consensus ? (
            <ProbabilityBar probs={match.consensus} label={t("card.consensus")} />
          ) : (
            <div className="flex h-7 items-center rounded-md bg-surface-muted px-2 text-xs text-muted">
              {t("card.noConsensus")}
            </div>
          )}
        </div>
      </Card>
    </Link>
  );
}
