"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { oneDecimal } from "@/lib/format";
import type { MethodPrediction } from "@/types/match";

import { ProbabilityBar } from "./ProbabilityBar";

/** One method's labelled 1X2 bar. The champion is flagged with its accuracy %. */
export function MethodBar({ prediction }: { prediction: MethodPrediction }) {
  const t = useTranslations();
  const label = t(`methods.${prediction.method}`);

  return (
    <div className="flex flex-col gap-1" data-testid={`method-bar-${prediction.method}`}>
      <div className="flex items-center justify-between text-sm">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          {label}
          {prediction.is_champion && (
            <Badge variant="brand" aria-label={t("card.champion")}>
              ★{" "}
              {prediction.accuracy_pct != null
                ? `${oneDecimal(prediction.accuracy_pct)}%`
                : t("card.champion")}
            </Badge>
          )}
        </span>
        {!prediction.is_champion && prediction.accuracy_pct != null && (
          <span className="text-xs text-muted">{oneDecimal(prediction.accuracy_pct)}%</span>
        )}
      </div>
      <ProbabilityBar probs={prediction.probs} label={label} />
    </div>
  );
}
