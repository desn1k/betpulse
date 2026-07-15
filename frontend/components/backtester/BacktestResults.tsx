"use client";

import { useTranslations } from "next-intl";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { pct } from "@/lib/format";
import type { BacktestResult } from "@/types/backtester";

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <span className="text-xs font-medium uppercase tracking-wide text-muted">{label}</span>
      <span className="text-2xl font-bold text-foreground">{value}</span>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </Card>
  );
}

export function BacktestResults({ result }: { result: BacktestResult }) {
  const t = useTranslations();

  if (result.matched_count === 0) {
    return (
      <p className="rounded-card bg-surface-muted p-6 text-center text-muted-strong">
        {t("backtester.empty")}
      </p>
    );
  }

  const roiPct = `${(result.roi * 100).toFixed(1)}%`;
  const equityData = result.equity_curve.map((y, i) => ({ i: i + 1, pnl: y }));

  return (
    <div className="flex flex-col gap-4">
      {/* Impossible to miss: a full yellow warning card above the results. */}
      {result.small_sample_warning && (
        <div
          role="alert"
          className="rounded-card border border-warn/40 bg-warn/10 p-4 text-sm font-medium text-warn"
        >
          {t("backtester.smallSample", { count: result.matched_count })}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label={t("backtester.metrics.matched")} value={String(result.matched_count)} />
        <Metric label={t("backtester.metrics.winRate")} value={pct(result.win_rate)} />
        <div className="flex flex-col gap-1">
          <Metric label={t("backtester.metrics.roi")} value={roiPct} />
          {/* Requirement: inline text next to the ROI card — not a tooltip. */}
          <p className="px-1 text-xs italic text-muted">{t("backtester.roiDisclaimerInline")}</p>
        </div>
        <Metric
          label={t("backtester.metrics.drawdown")}
          value={result.max_drawdown.toFixed(2)}
          hint={`${t("backtester.metrics.ci")}: ${pct(result.win_rate_ci.lower)}–${pct(
            result.win_rate_ci.upper,
          )}`}
        />
      </div>

      {result.walk_forward && result.out_of_sample_roi !== null && (
        <Badge variant="brand">
          {t("backtester.metrics.outOfSample")}: {(result.out_of_sample_roi * 100).toFixed(1)}%
        </Badge>
      )}

      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold text-muted-strong">{t("backtester.equity")}</h3>
        <div className="h-64 w-full" data-testid="equity-chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={equityData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
              <XAxis dataKey="i" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip />
              <Line type="monotone" dataKey="pnl" stroke="var(--color-brand)" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}
