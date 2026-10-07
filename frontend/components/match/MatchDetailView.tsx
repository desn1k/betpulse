"use client";

import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { DisclaimerBanner } from "@/components/legal/DisclaimerBanner";
import { ApiError } from "@/lib/api";
import { useMatch } from "@/lib/queries";

import { NotifyToggle } from "@/components/push/NotifyToggle";

import { AnalysisBlock } from "./AnalysisBlock";
import { ConsensusBar } from "./ConsensusBar";
import { MatchStatus } from "./MatchStatus";
import { MethodBars } from "./MethodBars";
import { MatchDetailSkeleton } from "./MatchDetailSkeleton";

function limitTierRequired(error: unknown): string | null {
  if (error instanceof ApiError && error.status === 403) {
    const detail = (error.body as { detail?: { tier_required?: string } } | null)?.detail;
    return detail?.tier_required ?? "pro";
  }
  return null;
}

export function MatchDetailView({ id }: { id: string }) {
  const t = useTranslations();
  const format = useFormatter();
  const query = useMatch(id);

  if (query.isPending) return <MatchDetailSkeleton />;
  if (query.data === undefined) {
    // First load failed: nothing to show. A 403 means the caller spent their
    // daily match-view budget.
    const upgradeTo = limitTierRequired(query.error);
    return (
      <Card className="flex flex-col items-center gap-3 p-8 text-center text-muted-strong">
        {upgradeTo ? (
          <>
            <span className="text-3xl" aria-hidden="true">
              🔒
            </span>
            <p className="text-lg font-semibold text-foreground">{t("detail.limitReached")}</p>
            <p role="alert">{t("detail.limitReachedBody", { tier: upgradeTo })}</p>
          </>
        ) : (
          <p role="alert">{t("detail.error")}</p>
        )}
        <Link href="/" className="mt-2 inline-block font-semibold text-brand">
          ← {t("detail.backToList")}
        </Link>
      </Card>
    );
  }

  const match = query.data;
  // A background refetch failed: keep the card the user is reading (F8). A 403
  // is the daily quota (spent, or lowered by a tier change); anything else may
  // be transient, so the data is only marked as possibly stale.
  const refetchError = query.isError ? query.error : null;
  const limitTier = limitTierRequired(refetchError);
  const updatedAt = format.dateTime(new Date(query.dataUpdatedAt), {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const methodsUnlocked =
    match.flags.methods === "all" || match.flags.methods === "all_weights";

  return (
    <div className="flex flex-col gap-6">
      <Link href="/" className="text-sm font-semibold text-brand">
        ← {t("detail.backToList")}
      </Link>

      {limitTier ? (
        <Card className="flex flex-col gap-1 p-4" role="status">
          <p className="font-semibold text-foreground">
            <span aria-hidden="true">🔒 </span>
            {t("detail.limitReached")}
          </p>
          <p className="text-sm text-muted-strong">
            {t("detail.limitReachedBody", { tier: limitTier })}
          </p>
          <p className="text-sm text-muted-strong">{t("detail.lastDataBelow")}</p>
        </Card>
      ) : refetchError ? (
        <p className="rounded-card bg-surface-muted px-4 py-2 text-sm text-muted-strong" role="status">
          {t("detail.staleNote", { time: updatedAt })}
        </p>
      ) : null}

      <Card className="flex flex-col gap-6 p-6">
        <header className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">
              {match.league.name}
            </span>
            <div className="flex items-center gap-2">
              {match.data_delayed && <Badge variant="warn">{t("card.dataDelayed")}</Badge>}
              <NotifyToggle id={id} />
              <MatchStatus match={match} />
            </div>
          </div>
          <div className="flex items-center justify-between gap-4 text-xl font-bold text-foreground">
            <span>{match.home_team}</span>
            {match.status !== "scheduled" && (
              <span className="tabular-nums text-muted-strong">
                {match.home_score ?? "–"} : {match.away_score ?? "–"}
              </span>
            )}
            <span className="text-right">{match.away_team}</span>
          </div>
        </header>

        <ConsensusBar match={match} />

        {/* Method bars come from the server only for pro/expert; other tiers get
            an empty list and see the locked placeholder. Enforcement is server-side. */}
        <MethodBars
          methods={match.methods}
          locked={!methodsUnlocked}
          tierRequired={match.tier_required}
        />
      </Card>

      <AnalysisBlock id={id} />

      <DisclaimerBanner />
    </div>
  );
}
