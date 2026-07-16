"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { fetchIngestionRuns, rescan } from "@/lib/admin";
import type { IngestionRuns, IngestionStatus } from "@/types/admin";

const LEAGUES = ["EPL", "LALIGA", "SERIEA", "BUNDESLIGA", "LIGUE1"];

const STATUS_VARIANT: Record<IngestionStatus, "brand" | "live" | "warn" | "neutral"> = {
  running: "brand",
  success: "live",
  partial: "warn",
  failed: "warn",
};

/** Poll while any job is still running, otherwise stop (do not poll forever).
 * `pollMs` is the configurable interval; default 5s. */
export function nextPollInterval(
  data: IngestionRuns | undefined,
  pollMs: number,
): number | false {
  return data?.runs.some((r) => r.status === "running") ? pollMs : false;
}

/** Poll while any job is running, then stop. `pollMs` is the (configurable)
 * interval; default 5s. */
export function IngestionView({ pollMs = 5000 }: { pollMs?: number }) {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [leagues, setLeagues] = useState<string[]>([]);
  const [seasons, setSeasons] = useState("");

  const runs = useQuery({
    queryKey: ["admin", "ingestion"],
    queryFn: () => fetchIngestionRuns(),
    // Stop polling automatically once nothing is running (do not poll forever).
    refetchInterval: (query) =>
      nextPollInterval(query.state.data as IngestionRuns | undefined, pollMs),
  });

  const seasonList = seasons
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const trigger = useMutation({
    mutationFn: () => rescan(leagues, seasonList),
    onSuccess: async () => {
      setLeagues([]);
      setSeasons("");
      await queryClient.invalidateQueries({ queryKey: ["admin", "ingestion"] });
    },
  });

  function toggleLeague(code: string) {
    setLeagues((ls) => (ls.includes(code) ? ls.filter((l) => l !== code) : [...ls, code]));
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-extrabold text-foreground">{t("admin.ingestion.title")}</h1>

      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.ingestion.rescan")}</h2>
        <fieldset className="flex flex-wrap gap-3">
          <legend className="sr-only">{t("admin.ingestion.leagues")}</legend>
          {LEAGUES.map((code) => (
            <label key={code} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={leagues.includes(code)}
                onChange={() => toggleLeague(code)}
              />
              {code}
            </label>
          ))}
        </fieldset>
        <label className="flex flex-col gap-1 text-sm">
          {t("admin.ingestion.seasons")}
          <input
            className="rounded-md border border-border bg-surface px-3 py-2"
            placeholder="2023-2024, 2024-2025"
            value={seasons}
            onChange={(e) => setSeasons(e.target.value)}
          />
          <span className="text-xs text-muted">{t("admin.ingestion.seasonsHint")}</span>
        </label>
        <div>
          <Button
            onClick={() => trigger.mutate()}
            disabled={leagues.length === 0 || seasonList.length === 0 || trigger.isPending}
          >
            {t("admin.ingestion.run")}
          </Button>
        </div>
      </Card>

      {runs.isPending ? (
        <div className="h-24 rounded-card bg-surface-muted" aria-busy="true" />
      ) : runs.data && runs.data.runs.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">{t("admin.ingestion.league")}</th>
                <th className="px-3 py-2">{t("admin.ingestion.season")}</th>
                <th className="px-3 py-2">{t("admin.ingestion.status")}</th>
                <th className="px-3 py-2">{t("admin.ingestion.fixtures")}</th>
                <th className="px-3 py-2">{t("admin.ingestion.odds")}</th>
                <th className="px-3 py-2">{t("admin.ingestion.duration")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.runs.map((r) => (
                <tr key={r.id} className="border-t border-border">
                  <td className="px-3 py-2 font-semibold text-foreground">{r.league ?? "—"}</td>
                  <td className="px-3 py-2 text-muted-strong">{r.season ?? "—"}</td>
                  <td className="px-3 py-2">
                    <Badge variant={STATUS_VARIANT[r.status]}>
                      {t(`admin.ingestion.statusLabel.${r.status}`)}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 tabular-nums">{r.fixtures_ingested}</td>
                  <td className="px-3 py-2 tabular-nums">{r.odds_ingested}</td>
                  <td className="px-3 py-2 tabular-nums text-muted">
                    {r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)}s` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted">{t("admin.ingestion.empty")}</p>
      )}
    </div>
  );
}
