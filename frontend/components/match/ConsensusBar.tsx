"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { oneDecimal, signedPp } from "@/lib/format";
import type { MatchDetail } from "@/types/match";

import { ProbabilityBar } from "./ProbabilityBar";

/**
 * The headline consensus bar with its trust signals: model-agreement % and
 * delta-vs-market. Always visible (even to guests) — it is the free tier's view.
 */
export function ConsensusBar({ match }: { match: MatchDetail }) {
  const t = useTranslations();
  if (match.consensus === null) return null;

  return (
    <section aria-label={t("card.consensus")} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-muted-strong">{t("card.consensus")}</h3>
        <div className="flex flex-wrap items-center gap-2">
          {match.model_agreement_pct != null && (
            <Badge variant="neutral">
              {t("card.agreement")}: {oneDecimal(match.model_agreement_pct)}%
            </Badge>
          )}
          {match.delta_vs_market != null && (
            <Badge variant={match.delta_vs_market >= 0 ? "brand" : "warn"}>
              {t("card.deltaVsMarket")}: {signedPp(match.delta_vs_market)}
            </Badge>
          )}
        </div>
      </div>
      <ProbabilityBar probs={match.consensus} label={t("card.consensus")} className="h-9" />
    </section>
  );
}
