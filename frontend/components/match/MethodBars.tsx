"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import type { MethodPrediction } from "@/types/match";

import { MethodBar } from "./MethodBar";
import { sortMethods } from "./methods";

interface MethodBarsProps {
  methods: MethodPrediction[];
  /**
   * When true, the per-method bars are shown blurred behind a tier lock — the
   * data is still delivered, but a guest/free user must upgrade to read it.
   * Server-side enforcement lands in Phase 7; this is the UX surface for it.
   */
  locked?: boolean;
  tierRequired?: string;
}

export function MethodBars({ methods, locked = false, tierRequired }: MethodBarsProps) {
  const t = useTranslations();
  const ordered = sortMethods(methods);

  return (
    <section aria-label={t("card.methods")} className="relative">
      <h3 className="mb-3 text-sm font-semibold text-muted-strong">{t("card.methods")}</h3>
      <div
        className={cn(
          "flex flex-col gap-3 transition",
          locked && "pointer-events-none select-none blur-sm",
        )}
        aria-hidden={locked}
      >
        {ordered.map((prediction) => (
          <MethodBar key={prediction.method} prediction={prediction} />
        ))}
      </div>

      {locked && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-card bg-surface/70 p-4 text-center">
          <span className="text-2xl" aria-hidden="true">
            🔒
          </span>
          <p className="text-sm font-semibold text-foreground">
            {t("card.tierLocked", { tier: tierRequired ?? "pro" })}
          </p>
        </div>
      )}
    </section>
  );
}
