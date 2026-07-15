"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ApiError, runBacktest } from "@/lib/api";
import { authHeader, useAuthStore } from "@/lib/auth/store";
import type { BacktestResult, BetType, RunRequest, StrategyFilter } from "@/types/backtester";

import { BacktestResults } from "./BacktestResults";

const PICKS: Record<BetType, string[]> = {
  "1x2": ["home", "draw", "away"],
  total: ["over", "under"],
};

function numberOrUndefined(v: string): number | undefined {
  if (v.trim() === "") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export function BacktesterView() {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);

  const [betType, setBetType] = useState<BetType>("1x2");
  const [pick, setPick] = useState("home");
  const [league, setLeague] = useState("");
  const [season, setSeason] = useState("");
  const [oddsMin, setOddsMin] = useState("");
  const [oddsMax, setOddsMax] = useState("");
  const [walkForward, setWalkForward] = useState(false);

  const [result, setResult] = useState<BacktestResult | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  function currentRequest(): RunRequest {
    const filters: StrategyFilter = {};
    if (league.trim()) filters.league = league.trim();
    if (season.trim()) filters.season = season.trim();
    const omin = numberOrUndefined(oddsMin);
    const omax = numberOrUndefined(oddsMax);
    if (omin !== undefined) filters.odds_min = omin;
    if (omax !== undefined) filters.odds_max = omax;
    return { bet_type: betType, pick, filters };
  }

  async function onRun(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setSaveMsg(null);
    try {
      setResult(await runBacktest(currentRequest(), walkForward));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        const tier =
          (err.body as { detail?: { tier_required?: string } } | null)?.detail?.tier_required ??
          "pro";
        setError(t("backtester.limitReached", { tier }));
      } else {
        setError(t("list.error"));
      }
      setResult(null);
    } finally {
      setPending(false);
    }
  }

  async function onSave() {
    setSaveMsg(null);
    const req = currentRequest();
    const res = await fetch("/api/backtester/strategies", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json", ...authHeader() },
      body: JSON.stringify({ name: `${betType}/${pick}`, ...req }),
    });
    setSaveMsg(res.status === 201 ? t("backtester.saved") : t("backtester.saveUpgrade"));
  }

  function changeBetType(next: BetType) {
    setBetType(next);
    setPick(PICKS[next][0]);
  }

  if (!user) {
    return (
      <p className="rounded-card bg-surface-muted p-6 text-center text-muted-strong">
        {t("backtester.loginRequired")}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Card className="p-4">
        <form onSubmit={onRun} className="flex flex-col gap-4" aria-label={t("backtester.title")}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-muted-strong">{t("backtester.betType")}</span>
              <select
                value={betType}
                onChange={(e) => changeBetType(e.target.value as BetType)}
                className="rounded-md border border-border bg-surface px-3 py-2"
              >
                <option value="1x2">1X2</option>
                <option value="total">{t("backtester.picks.over").replace(/ .*/, "")}/U 2.5</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-muted-strong">{t("backtester.pick")}</span>
              <select
                value={pick}
                onChange={(e) => setPick(e.target.value)}
                className="rounded-md border border-border bg-surface px-3 py-2"
              >
                {PICKS[betType].map((p) => (
                  <option key={p} value={p}>
                    {t(`backtester.picks.${p}`)}
                  </option>
                ))}
              </select>
            </label>
            <Field label={t("backtester.league")} value={league} onChange={setLeague} />
            <Field label={t("backtester.season")} value={season} onChange={setSeason} />
            <Field label={t("backtester.oddsMin")} value={oddsMin} onChange={setOddsMin} type="number" />
            <Field label={t("backtester.oddsMax")} value={oddsMax} onChange={setOddsMax} type="number" />
          </div>
          <label className="flex items-center gap-2 text-sm text-muted-strong">
            <input
              type="checkbox"
              checked={walkForward}
              onChange={(e) => setWalkForward(e.target.checked)}
            />
            {t("backtester.walkForward")}
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={pending}>
              {pending ? t("backtester.running") : t("backtester.run")}
            </Button>
            {result && (
              <Button type="button" variant="secondary" onClick={onSave}>
                {t("backtester.save")}
              </Button>
            )}
            {saveMsg && <span className="text-sm text-muted-strong">{saveMsg}</span>}
          </div>
        </form>
      </Card>

      {error && (
        <p role="alert" className="rounded-card bg-warn/10 p-4 text-sm font-medium text-warn">
          {error}
        </p>
      )}
      {result && <BacktestResults result={result} />}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-muted-strong">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border bg-surface px-3 py-2"
      />
    </label>
  );
}
