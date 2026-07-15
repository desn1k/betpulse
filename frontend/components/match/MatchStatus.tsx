"use client";

import { useFormatter, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import type { MatchSummary } from "@/types/match";

/** Kickoff time (scheduled) or a live-minute badge (live/finished). */
export function MatchStatus({ match }: { match: MatchSummary }) {
  const t = useTranslations();
  const format = useFormatter();

  if (match.status === "live") {
    return (
      <Badge variant="live">
        <span aria-hidden="true">●</span> {t("card.live")}
        {match.minute != null ? ` ${match.minute}'` : ""}
      </Badge>
    );
  }

  if (match.status === "finished") {
    return <Badge variant="neutral">{t("card.finished")}</Badge>;
  }

  const kickoff = new Date(match.kickoff_at);
  return (
    <time dateTime={match.kickoff_at} className="text-sm font-medium text-muted-strong">
      {format.dateTime(kickoff, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })}
    </time>
  );
}
