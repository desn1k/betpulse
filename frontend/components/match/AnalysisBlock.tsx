"use client";

import { useLocale, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import { useAnalysis } from "@/lib/queries";

// Pull ``detail.tier_required`` off a 403 so the lock names the tier to upgrade to.
function lockedTier(error: unknown): string | null {
  if (error instanceof ApiError && error.status === 403) {
    const detail = (error.body as { detail?: { tier_required?: string } } | null)?.detail;
    return detail?.tier_required ?? "free";
  }
  return null;
}

function formatResetTime(iso: string | null, locale: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

// The disclaimer note that always accompanies the AI text — the narrative
// explains the model outputs and is never itself a source of probabilities.
function AnalysisDisclaimer() {
  const t = useTranslations();
  return (
    <p className="text-xs text-muted" role="note">
      {t("analysis.disclaimer")}
    </p>
  );
}

export function AnalysisBlock({ id }: { id: string }) {
  const t = useTranslations();
  const locale = useLocale();
  const query = useAnalysis(id, locale);

  if (query.isPending) {
    return (
      <Card className="flex flex-col gap-3 p-6" aria-busy="true" aria-label={t("analysis.title")}>
        <h3 className="text-sm font-semibold text-muted-strong">{t("analysis.title")}</h3>
        <div className="h-4 w-full rounded bg-surface-muted" />
        <div className="h-4 w-4/5 rounded bg-surface-muted" />
      </Card>
    );
  }

  if (query.isError) {
    const tier = lockedTier(query.error);
    // Only the tier lock is worth surfacing; any other error hides the block.
    if (!tier) return null;
    return (
      <Card className="flex flex-col items-center gap-2 p-6 text-center">
        <span className="text-2xl" aria-hidden="true">
          🔒
        </span>
        <h3 className="text-sm font-semibold text-foreground">{t("analysis.title")}</h3>
        <p className="text-sm text-muted-strong">{t("analysis.locked", { tier })}</p>
      </Card>
    );
  }

  const result = query.data;

  // The feature is off, or there is nothing to explain for this match yet.
  if (result.status === "disabled" || result.status === "no_data") return null;

  if (result.status === "budget_exhausted") {
    return (
      <Card className="flex flex-col gap-2 p-6">
        <h3 className="text-sm font-semibold text-muted-strong">{t("analysis.title")}</h3>
        <p className="text-sm text-foreground" role="status">
          {t("analysis.budgetExhausted", {
            time: formatResetTime(result.resets_at, locale),
          })}
        </p>
        <AnalysisDisclaimer />
      </Card>
    );
  }

  return (
    <Card className="flex flex-col gap-3 p-6">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-muted-strong">{t("analysis.title")}</h3>
        {result.is_match_of_the_day && <Badge variant="brand">{t("analysis.matchOfTheDay")}</Badge>}
      </div>
      <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">
        {result.content}
      </p>
      <AnalysisDisclaimer />
    </Card>
  );
}
