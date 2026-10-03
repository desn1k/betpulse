"use client";

import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import type { MethodPrediction } from "@/types/match";

import { ProbabilityBar } from "./ProbabilityBar";

/** One method's labelled 1X2 bar. The champion is flagged with its Brier index,
 * always labelled as retrospective (a past-matches score, not a quality claim). */
export function MethodBar({ prediction }: { prediction: MethodPrediction }) {
  const t = useTranslations();
  const locale = useLocale();
  const label = t(`methods.${prediction.method}`);
  const retro =
    prediction.accuracy_pct == null
      ? null
      : t("card.retroValue", {
          value: new Intl.NumberFormat(locale, {
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
          }).format(prediction.accuracy_pct),
        });

  return (
    <div className="flex flex-col gap-1" data-testid={`method-bar-${prediction.method}`}>
      <div className="flex items-center justify-between text-sm">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          {label}
          {prediction.is_champion && (
            <Badge
              variant="brand"
              aria-label={t("card.champion")}
              title={retro ? t("card.retroHint") : undefined}
            >
              ★ {retro ?? t("card.champion")}
            </Badge>
          )}
        </span>
        {!prediction.is_champion && retro && (
          <span className="text-xs text-muted" title={t("card.retroHint")}>
            {retro}
          </span>
        )}
      </div>
      <ProbabilityBar probs={prediction.probs} label={label} />
    </div>
  );
}
