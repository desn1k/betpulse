"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { fetchLlmConfig, fetchSpend, updateLlmConfig } from "@/lib/admin";
import type { LlmConfigUpdate } from "@/types/admin";

const WINDOWS = [7, 30, 90] as const;

export function SpendView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [days, setDays] = useState<number>(30);

  const spend = useQuery({ queryKey: ["admin", "spend", days], queryFn: () => fetchSpend(days) });
  const config = useQuery({ queryKey: ["admin", "llm-config"], queryFn: fetchLlmConfig });

  const [form, setForm] = useState<LlmConfigUpdate>({});
  useEffect(() => {
    if (config.data) {
      setForm({
        model: config.data.model,
        daily_token_budget: config.data.daily_token_budget,
        max_tokens: config.data.max_tokens,
        is_enabled: config.data.is_enabled,
      });
    }
  }, [config.data]);

  const save = useMutation({
    mutationFn: (changes: LlmConfigUpdate) => updateLlmConfig(changes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "llm-config"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "spend"] });
    },
  });

  const budget = spend.data?.daily_token_budget ?? 0;
  const chartData = (spend.data?.daily ?? []).map((d) => ({
    day: d.day.slice(5), // MM-DD
    tokens: d.tokens_in + d.tokens_out,
    cost: Number(d.cost),
  }));

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-extrabold text-foreground">{t("admin.spend.title")}</h1>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <Button
              key={w}
              size="sm"
              variant={days === w ? "primary" : "secondary"}
              onClick={() => setDays(w)}
            >
              {t("admin.spend.days", { days: w })}
            </Button>
          ))}
        </div>
      </div>

      {/* Totals */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs uppercase tracking-wide text-muted">
            {t("admin.spend.totalCost")}
          </span>
          <span className="text-2xl font-bold text-foreground tabular-nums">
            ${Number(spend.data?.total_cost ?? 0).toFixed(2)}
          </span>
        </Card>
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs uppercase tracking-wide text-muted">
            {t("admin.spend.totalTokens")}
          </span>
          <span className="text-2xl font-bold text-foreground tabular-nums">
            {(spend.data?.total_tokens ?? 0).toLocaleString()}
          </span>
        </Card>
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs uppercase tracking-wide text-muted">
            {t("admin.spend.dailyBudget")}
          </span>
          <span className="text-2xl font-bold text-foreground tabular-nums">
            {budget.toLocaleString()}
          </span>
        </Card>
      </div>

      {/* Daily tokens vs budget */}
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.spend.dailyTokens")}</h2>
        {spend.isPending ? (
          <div className="h-56 rounded-card bg-surface-muted" aria-busy="true" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted">{t("admin.spend.empty")}</p>
        ) : (
          <div className="h-56 w-full" data-testid="spend-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip />
                {budget > 0 && (
                  <ReferenceLine
                    y={budget}
                    stroke="var(--color-warn, #d97706)"
                    strokeDasharray="4 4"
                    label={{ value: t("admin.spend.budgetLine"), fontSize: 10 }}
                  />
                )}
                <Bar dataKey="tokens" fill="var(--color-brand, #2563eb)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      {/* Top fixtures by cost */}
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.spend.topFixtures")}</h2>
        {spend.data && spend.data.top_fixtures.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-2 py-2">{t("admin.spend.fixture")}</th>
                  <th className="px-2 py-2">{t("admin.spend.league")}</th>
                  <th className="px-2 py-2">{t("admin.spend.cost")}</th>
                  <th className="px-2 py-2">{t("admin.spend.tokens")}</th>
                  <th className="px-2 py-2">{t("admin.spend.calls")}</th>
                </tr>
              </thead>
              <tbody>
                {spend.data.top_fixtures.map((f) => (
                  <tr key={f.fixture_id} className="border-t border-border">
                    <td className="px-2 py-2 font-semibold text-foreground">
                      {f.home} – {f.away}
                    </td>
                    <td className="px-2 py-2 text-muted-strong">{f.league}</td>
                    <td className="px-2 py-2 tabular-nums">${Number(f.cost).toFixed(4)}</td>
                    <td className="px-2 py-2 tabular-nums">
                      {(f.tokens_in + f.tokens_out).toLocaleString()}
                    </td>
                    <td className="px-2 py-2 tabular-nums">{f.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted">{t("admin.spend.empty")}</p>
        )}
      </Card>

      {/* LLM config editor */}
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.spend.config")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.spend.model")}
            <input
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.model ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.spend.apiKey")}
            <input
              type="password"
              placeholder={config.data?.key_masked ?? ""}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.api_key ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.spend.dailyBudget")}
            <input
              type="number"
              aria-label={t("admin.spend.dailyBudget")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.daily_token_budget ?? 0}
              onChange={(e) =>
                setForm((f) => ({ ...f, daily_token_budget: Number(e.target.value) }))
              }
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.spend.maxTokens")}
            <input
              type="number"
              aria-label={t("admin.spend.maxTokens")}
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.max_tokens ?? 0}
              onChange={(e) => setForm((f) => ({ ...f, max_tokens: Number(e.target.value) }))}
            />
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.is_enabled ?? false}
            onChange={(e) => setForm((f) => ({ ...f, is_enabled: e.target.checked }))}
          />
          {t("admin.spend.enabled")}
        </label>
        <div>
          <Button
            onClick={() => {
              // Only send api_key when the admin actually typed a new one.
              const changes = { ...form };
              if (!changes.api_key) delete changes.api_key;
              save.mutate(changes);
            }}
            disabled={save.isPending}
          >
            {t("admin.spend.save")}
          </Button>
        </div>
      </Card>
    </div>
  );
}
