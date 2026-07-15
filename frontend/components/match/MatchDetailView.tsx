"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { DisclaimerBanner } from "@/components/legal/DisclaimerBanner";
import { useMatch } from "@/lib/queries";

import { ConsensusBar } from "./ConsensusBar";
import { MatchStatus } from "./MatchStatus";
import { MethodBars } from "./MethodBars";
import { MatchDetailSkeleton } from "./MatchDetailSkeleton";

export function MatchDetailView({ id }: { id: string }) {
  const t = useTranslations();
  const query = useMatch(id);

  if (query.isPending) return <MatchDetailSkeleton />;
  if (query.isError) {
    return (
      <Card className="p-8 text-center text-muted-strong">
        <p role="alert">{t("detail.error")}</p>
        <Link href="/" className="mt-4 inline-block font-semibold text-brand">
          ← {t("detail.backToList")}
        </Link>
      </Card>
    );
  }

  const match = query.data;

  return (
    <div className="flex flex-col gap-6">
      <Link href="/" className="text-sm font-semibold text-brand">
        ← {t("detail.backToList")}
      </Link>

      <Card className="flex flex-col gap-6 p-6">
        <header className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">
              {match.league.name}
            </span>
            <div className="flex items-center gap-2">
              {match.data_delayed && <Badge variant="warn">{t("card.dataDelayed")}</Badge>}
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

        {/* Phase 7 supplies the viewer's tier to decide `locked`; until then the
            data is shown, with the lock UX already wired via tier_required. */}
        {match.methods.length > 0 && (
          <MethodBars methods={match.methods} locked={false} tierRequired={match.tier_required} />
        )}
      </Card>

      <DisclaimerBanner />
    </div>
  );
}
