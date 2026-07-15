"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { ApiError, redeemPromo, type RedeemEffect } from "@/lib/api";
import { matchKeys } from "@/lib/queries";

type Msg = { ok: boolean; text: string } | null;

function errorKey(status: number): string {
  switch (status) {
    case 404:
      return "promo.errors.invalid";
    case 410:
      return "promo.errors.expired";
    case 409:
      return "promo.errors.used";
    case 403:
      return "promo.errors.notYours";
    case 429:
      return "promo.errors.rateLimited";
    default:
      return "promo.errors.generic";
  }
}

export function RedeemPromo({ onApplied }: { onApplied?: () => void }) {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<Msg>(null);

  function effectMessage(effect: RedeemEffect): string {
    if (effect.status === "applied") return t("promo.applied");
    if (effect.type === "percent") return t("promo.pendingPercent", { value: effect.value ?? "" });
    if (effect.type === "fixed") return t("promo.pendingFixed", { value: effect.value ?? "" });
    return t("promo.applied");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setMessage(null);
    try {
      const effect = await redeemPromo(code.trim());
      // A trial/upgrade changes the tier server-side → refetch gated data.
      await queryClient.invalidateQueries({ queryKey: matchKeys.all });
      setMessage({ ok: true, text: effectMessage(effect) });
      setCode("");
      onApplied?.();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setMessage({ ok: false, text: t(errorKey(status)) });
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" aria-label={t("promo.title")}>
      <label className="text-sm font-medium text-muted-strong" htmlFor="promo-code">
        {t("promo.title")}
      </label>
      <div className="flex gap-2">
        <input
          id="promo-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={t("promo.placeholder")}
          required
          className="min-w-0 flex-1 rounded-md border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-brand"
        />
        <Button type="submit" size="sm" disabled={pending || code.trim().length === 0}>
          {t("promo.submit")}
        </Button>
      </div>
      {message && (
        <p
          role={message.ok ? "status" : "alert"}
          className={message.ok ? "text-sm text-brand-strong" : "text-sm text-live"}
        >
          {message.text}
        </p>
      )}
    </form>
  );
}
