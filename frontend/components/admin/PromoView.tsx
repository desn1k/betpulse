"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { createBatch, fetchBatches, fetchTiers, killBatch } from "@/lib/admin";
import type { PromoBatchCreated, PromoCodeType } from "@/types/admin";

const CODE_TYPES: PromoCodeType[] = ["percent", "fixed", "trial", "upgrade"];

const emptyForm = {
  name: "",
  code_type: "upgrade" as PromoCodeType,
  size: 500,
  value: "",
  tier_id: "",
  bind: false,
  bound_user_id: "",
  max_activations: 1,
  expires_at: "",
  stackable: false,
};

/** Trigger a client-side download of the one-time plaintext codes as CSV. */
function downloadCodes(name: string, codes: string[]) {
  const blob = new Blob([`code\n${codes.join("\n")}\n`], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `promo_${name || "batch"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function PromoView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);
  const [created, setCreated] = useState<PromoBatchCreated | null>(null);

  const batches = useQuery({ queryKey: ["admin", "promo"], queryFn: fetchBatches });
  const tiers = useQuery({ queryKey: ["admin", "tiers"], queryFn: fetchTiers });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", "promo"] });

  const generate = useMutation({
    mutationFn: () =>
      createBatch({
        name: form.name,
        code_type: form.code_type,
        size: form.size,
        // Value is unused for upgrade codes.
        value: form.code_type === "upgrade" ? null : form.value || null,
        tier_id: form.tier_id || null,
        bound_user_id: form.bind ? form.bound_user_id || null : null,
        max_activations: form.max_activations,
        expires_at: form.expires_at ? new Date(form.expires_at).toISOString() : null,
        stackable: form.stackable,
      }),
    onSuccess: async (result) => {
      setCreated(result);
      setForm(emptyForm);
      await invalidate();
    },
  });

  const kill = useMutation({ mutationFn: killBatch, onSuccess: invalidate });

  const sizeValid = form.size > 0 && form.size % 500 === 0;
  const isUpgrade = form.code_type === "upgrade";

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-extrabold text-foreground">{t("admin.promo.title")}</h1>

      {/* Generate form */}
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.promo.generate")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.name")}
            <input
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.codeType")}
            <select
              aria-label={t("admin.promo.codeType")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.code_type}
              onChange={(e) =>
                setForm((f) => ({ ...f, code_type: e.target.value as PromoCodeType }))
              }
            >
              {CODE_TYPES.map((ct) => (
                <option key={ct} value={ct}>
                  {ct}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.size")}
            <input
              type="number"
              step={500}
              min={500}
              aria-label={t("admin.promo.size")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.size}
              onChange={(e) => setForm((f) => ({ ...f, size: Number(e.target.value) }))}
            />
            {!sizeValid && <span className="text-xs text-warn">{t("admin.promo.sizeHint")}</span>}
          </label>
          {/* Value is hidden for upgrade codes (unused). */}
          {!isUpgrade && (
            <label className="flex flex-col gap-1 text-sm">
              {t("admin.promo.value")}
              <input
                type="number"
                aria-label={t("admin.promo.value")}
                className="rounded-md border border-border bg-surface px-3 py-2"
                value={form.value}
                onChange={(e) => setForm((f) => ({ ...f, value: e.target.value }))}
              />
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.tier")}
            <select
              aria-label={t("admin.promo.tier")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.tier_id}
              onChange={(e) => setForm((f) => ({ ...f, tier_id: e.target.value }))}
            >
              <option value="">—</option>
              {tiers.data
                ?.filter((tr) => tr.name !== "guest")
                .map((tr) => (
                  <option key={tr.id} value={tr.id}>
                    {tr.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.maxActivations")}
            <input
              type="number"
              min={1}
              aria-label={t("admin.promo.maxActivations")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.max_activations}
              onChange={(e) => setForm((f) => ({ ...f, max_activations: Number(e.target.value) }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.expiresAt")}
            <input
              type="date"
              aria-label={t("admin.promo.expiresAt")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.expires_at}
              onChange={(e) => setForm((f) => ({ ...f, expires_at: e.target.value }))}
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.stackable}
              onChange={(e) => setForm((f) => ({ ...f, stackable: e.target.checked }))}
            />
            {t("admin.promo.stackable")}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.bind}
              onChange={(e) => setForm((f) => ({ ...f, bind: e.target.checked }))}
            />
            {t("admin.promo.bind")}
          </label>
        </div>
        {/* Bound-user field only appears when the admin opts in. */}
        {form.bind && (
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.promo.boundUser")}
            <input
              aria-label={t("admin.promo.boundUser")}
              placeholder={t("admin.promo.boundUserHint")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.bound_user_id}
              onChange={(e) => setForm((f) => ({ ...f, bound_user_id: e.target.value }))}
            />
          </label>
        )}
        <div>
          <Button
            onClick={() => generate.mutate()}
            disabled={!form.name || !sizeValid || generate.isPending}
          >
            {t("admin.promo.generateButton")}
          </Button>
        </div>
      </Card>

      {/* One-time plaintext codes */}
      {created && (
        <Card className="flex flex-col gap-3 border-warn/40 bg-warn/5 p-6" role="status">
          <h2 className="text-sm font-semibold text-warn">{t("admin.promo.codesTitle")}</h2>
          <p className="text-xs text-muted-strong">{t("admin.promo.codesWarning")}</p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => downloadCodes(created.batch.name, created.codes)}>
              {t("admin.promo.download")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>
              {t("admin.promo.dismiss")}
            </Button>
          </div>
          <p className="text-xs text-muted">
            {t("admin.promo.codesCount", { count: created.codes.length })}
          </p>
        </Card>
      )}

      {/* Batch list */}
      {batches.data && batches.data.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-2 py-2">{t("admin.promo.name")}</th>
                <th className="px-2 py-2">{t("admin.promo.codeType")}</th>
                <th className="px-2 py-2">{t("admin.promo.size")}</th>
                <th className="px-2 py-2">{t("admin.promo.status")}</th>
                <th className="px-2 py-2">{t("admin.promo.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {batches.data.map((b) => (
                <tr key={b.id} className="border-t border-border">
                  <td className="px-2 py-2 font-semibold text-foreground">{b.name}</td>
                  <td className="px-2 py-2 text-muted-strong">{b.code_type}</td>
                  <td className="px-2 py-2 tabular-nums">{b.size}</td>
                  <td className="px-2 py-2">
                    <Badge variant={b.status === "active" ? "brand" : "neutral"}>{b.status}</Badge>
                  </td>
                  <td className="px-2 py-2">
                    {b.status === "active" && (
                      <Button variant="ghost" size="sm" onClick={() => kill.mutate(b.id)}>
                        {t("admin.promo.kill")}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted">{t("admin.promo.empty")}</p>
      )}
    </div>
  );
}
