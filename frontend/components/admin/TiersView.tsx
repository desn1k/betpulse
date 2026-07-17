"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { fetchTiers, updateTier } from "@/lib/admin";
import type { Tier, TierUpdate } from "@/types/admin";

interface Draft {
  price: string;
  is_public: boolean;
  feature_flags: string;
  limits: string;
  error: string | null;
}

function toDraft(tier: Tier): Draft {
  return {
    price: tier.price,
    is_public: tier.is_public,
    feature_flags: JSON.stringify(tier.feature_flags, null, 2),
    limits: JSON.stringify(tier.limits, null, 2),
    error: null,
  };
}

export function TiersView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});

  const tiers = useQuery({ queryKey: ["admin", "tiers"], queryFn: fetchTiers });

  // Seed one draft per tier when the list loads.
  useEffect(() => {
    if (tiers.data) {
      setDrafts(Object.fromEntries(tiers.data.map((tr) => [tr.id, toDraft(tr)])));
    }
  }, [tiers.data]);

  const save = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: TierUpdate }) => updateTier(id, changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin", "tiers"] }),
  });

  function patch(id: string, next: Partial<Draft>) {
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...next } }));
  }

  function submit(id: string) {
    const draft = drafts[id];
    let feature_flags: Record<string, unknown>;
    let limits: Record<string, unknown>;
    try {
      feature_flags = JSON.parse(draft.feature_flags);
      limits = JSON.parse(draft.limits);
    } catch {
      patch(id, { error: t("admin.tiers.jsonError") });
      return;
    }
    patch(id, { error: null });
    save.mutate({
      id,
      changes: { price: draft.price, is_public: draft.is_public, feature_flags, limits },
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-extrabold text-foreground">{t("admin.tiers.title")}</h1>

      {tiers.isPending ? (
        <div className="h-40 rounded-card bg-surface-muted" aria-busy="true" />
      ) : (
        tiers.data?.map((tr) => {
          const draft = drafts[tr.id];
          if (!draft) return null;
          return (
            <Card key={tr.id} className="flex flex-col gap-3 p-6">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold text-foreground">{tr.name}</h2>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    aria-label={`${tr.name} public`}
                    checked={draft.is_public}
                    onChange={(e) => patch(tr.id, { is_public: e.target.checked })}
                  />
                  {t("admin.tiers.public")}
                </label>
              </div>
              <label className="flex max-w-xs flex-col gap-1 text-sm">
                {t("admin.tiers.price")}
                <input
                  type="number"
                  step="0.01"
                  aria-label={`${tr.name} price`}
                  className="rounded-md border border-border bg-surface px-3 py-2"
                  value={draft.price}
                  onChange={(e) => patch(tr.id, { price: e.target.value })}
                />
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  {t("admin.tiers.featureFlags")}
                  <textarea
                    aria-label={`${tr.name} feature_flags`}
                    className="h-40 rounded-md border border-border bg-surface px-3 py-2 font-mono text-xs"
                    value={draft.feature_flags}
                    onChange={(e) => patch(tr.id, { feature_flags: e.target.value })}
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  {t("admin.tiers.limits")}
                  <textarea
                    aria-label={`${tr.name} limits`}
                    className="h-40 rounded-md border border-border bg-surface px-3 py-2 font-mono text-xs"
                    value={draft.limits}
                    onChange={(e) => patch(tr.id, { limits: e.target.value })}
                  />
                </label>
              </div>
              {draft.error && (
                <p className="text-sm text-warn" role="alert">
                  {draft.error}
                </p>
              )}
              <div>
                <Button size="sm" onClick={() => submit(tr.id)} disabled={save.isPending}>
                  {t("admin.tiers.save")}
                </Button>
              </div>
            </Card>
          );
        })
      )}
    </div>
  );
}
