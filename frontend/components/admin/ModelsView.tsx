"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  demoteModel,
  fetchModels,
  fetchSnapshotDiff,
  fetchSnapshots,
  patchModel,
  promoteModel,
  retrainModels,
  rollbackSnapshot,
  setWeightingMode,
  setWeights as saveWeights,
} from "@/lib/admin";
import type { ModelStatus, RollbackChange } from "@/types/admin";

const STATUS_VARIANT: Record<ModelStatus, "brand" | "live" | "neutral"> = {
  champion: "brand",
  challenger: "neutral",
  retired: "neutral",
};

function pct(value: number | null): string {
  return value == null ? "—" : value.toFixed(1);
}

export function ModelsView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [warning, setWarning] = useState<string | null>(null);
  const [diff, setDiff] = useState<{ id: string; changes: RollbackChange[] } | null>(null);

  const models = useQuery({ queryKey: ["admin", "models"], queryFn: fetchModels });
  const snapshots = useQuery({ queryKey: ["admin", "snapshots"], queryFn: fetchSnapshots });
  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] }),
      queryClient.invalidateQueries({ queryKey: ["admin", "snapshots"] }),
    ]);

  const mode = models.data?.weighting_mode ?? "auto";
  const manual = mode === "manual";

  // Seed the editable weights from the server whenever the model list changes.
  useEffect(() => {
    if (models.data) {
      setWeights(Object.fromEntries(models.data.models.map((m) => [m.method, m.display_weight])));
    }
  }, [models.data]);

  const modeMutation = useMutation({
    mutationFn: setWeightingMode,
    onSuccess: invalidate,
  });
  const weightsMutation = useMutation({ mutationFn: saveWeights, onSuccess: invalidate });
  const toggle = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Record<string, boolean> }) =>
      patchModel(id, changes),
    onSuccess: invalidate,
  });
  const promote = useMutation({
    mutationFn: promoteModel,
    onSuccess: async (result) => {
      setWarning(result.warning);
      await invalidate();
    },
  });
  const demote = useMutation({ mutationFn: demoteModel, onSuccess: invalidate });
  const retrain = useMutation({ mutationFn: retrainModels });
  const rollback = useMutation({
    mutationFn: rollbackSnapshot,
    onSuccess: async () => {
      setDiff(null);
      await invalidate();
    },
  });

  async function preview(id: string) {
    const d = await fetchSnapshotDiff(id);
    setDiff({ id, changes: d.changes });
  }

  const weightSum = Object.values(weights).reduce((a, b) => a + Number(b || 0), 0);
  const weightsValid = Math.abs(weightSum - 100) < 0.05;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-extrabold text-foreground">{t("admin.models.title")}</h1>
        <Button variant="secondary" onClick={() => retrain.mutate()} disabled={retrain.isPending}>
          {t("admin.models.retrain")}
        </Button>
      </div>

      {/* Weighting mode */}
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <span className="text-sm font-semibold text-muted-strong">{t("admin.models.weighting")}</span>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant={mode === "auto" ? "primary" : "secondary"}
            onClick={() => modeMutation.mutate("auto")}
          >
            {t("admin.models.auto")}
          </Button>
          <Button
            size="sm"
            variant={manual ? "primary" : "secondary"}
            onClick={() => modeMutation.mutate("manual")}
          >
            {t("admin.models.manual")}
          </Button>
        </div>
        {manual && (
          <div className="flex items-center gap-2 text-sm">
            <span className={weightsValid ? "text-muted" : "text-warn"}>
              {t("admin.models.sum", { sum: weightSum.toFixed(1) })}
            </span>
            <Button
              size="sm"
              onClick={() => weightsMutation.mutate(weights)}
              disabled={!weightsValid || weightsMutation.isPending}
            >
              {t("admin.models.saveWeights")}
            </Button>
          </div>
        )}
      </Card>

      {warning && (
        <p className="text-sm text-warn" role="status">
          {t(`admin.models.warning.${warning}`)}
        </p>
      )}

      {models.isPending ? (
        <div className="h-40 rounded-card bg-surface-muted" aria-busy="true" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-2 py-2">{t("admin.models.method")}</th>
                <th className="px-2 py-2">{t("admin.models.status")}</th>
                <th className="px-2 py-2">{t("admin.models.accuracy")}</th>
                <th className="px-2 py-2">{t("admin.models.roi")}</th>
                <th className="px-2 py-2">{t("admin.models.samples")}</th>
                <th className="px-2 py-2">{t("admin.models.weight")}</th>
                <th className="px-2 py-2">{t("admin.models.visible")}</th>
                <th className="px-2 py-2">{t("admin.models.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {models.data?.models.map((m) => (
                <tr key={m.id} className="border-t border-border">
                  <td className="px-2 py-2 font-semibold text-foreground">
                    {m.method}
                    <span className="ml-1 text-xs text-muted">{m.version}</span>
                  </td>
                  <td className="px-2 py-2">
                    <Badge variant={STATUS_VARIANT[m.status]}>
                      {t(`admin.models.statusLabel.${m.status}`)}
                    </Badge>
                  </td>
                  <td className="px-2 py-2 tabular-nums">{pct(m.accuracy_pct)}</td>
                  <td className="px-2 py-2 tabular-nums">{pct(m.roi_vs_closing)}</td>
                  <td className="px-2 py-2 tabular-nums">{m.sample_count}</td>
                  <td className="px-2 py-2 tabular-nums">
                    {manual ? (
                      <input
                        type="number"
                        aria-label={`${m.method} weight`}
                        className="w-20 rounded border border-border bg-surface px-2 py-1"
                        value={weights[m.method] ?? 0}
                        onChange={(e) =>
                          setWeights((w) => ({ ...w, [m.method]: Number(e.target.value) }))
                        }
                      />
                    ) : (
                      m.display_weight.toFixed(1)
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      aria-label={`${m.method} visible`}
                      checked={m.is_visible}
                      onChange={() =>
                        toggle.mutate({ id: m.id, changes: { is_visible: !m.is_visible } })
                      }
                    />
                  </td>
                  <td className="flex gap-1 px-2 py-2">
                    {m.status === "champion" ? (
                      <Button variant="ghost" size="sm" onClick={() => demote.mutate(m.id)}>
                        {t("admin.models.demote")}
                      </Button>
                    ) : (
                      <Button variant="secondary" size="sm" onClick={() => promote.mutate(m.id)}>
                        {t("admin.models.promote")}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Snapshots + rollback */}
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.models.snapshots")}</h2>
        {snapshots.data && snapshots.data.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {snapshots.data.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-strong">{s.reason}</span>
                <span className="text-xs text-muted">{new Date(s.taken_at).toLocaleString()}</span>
                <Button variant="ghost" size="sm" onClick={() => preview(s.id)}>
                  {t("admin.models.preview")}
                </Button>
                <Button variant="secondary" size="sm" onClick={() => rollback.mutate(s.id)}>
                  {t("admin.models.rollback")}
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">{t("admin.models.noSnapshots")}</p>
        )}

        {diff && (
          <div className="rounded-md border border-border p-3 text-sm">
            <p className="mb-2 font-semibold">{t("admin.models.diffTitle")}</p>
            {diff.changes.length === 0 ? (
              <p className="text-muted">{t("admin.models.noChanges")}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {diff.changes.map((c) => (
                  <li key={`${c.method}-${c.version}`} className="tabular-nums text-muted-strong">
                    {c.method}: {c.status_from} → {c.status_to} · {c.weight_from} → {c.weight_to}
                    {c.enabled_from !== c.enabled_to &&
                      ` · enabled ${c.enabled_from} → ${c.enabled_to}`}
                    {c.visible_from !== c.visible_to &&
                      ` · visible ${c.visible_from} → ${c.visible_to}`}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
