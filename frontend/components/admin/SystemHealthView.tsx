"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { fetchSystemHealth, sendTestAlert } from "@/lib/admin";
import { cn } from "@/lib/utils";
import type { HealthStatus } from "@/types/admin";

function formatMetaValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

const STATUS_CLASS: Record<HealthStatus | "system", string> = {
  ok: "bg-brand-soft text-brand-strong",
  degraded: "bg-amber-100 text-amber-800",
  error: "bg-red-100 text-red-700",
  not_configured: "bg-surface-muted text-muted-strong",
  system: "bg-surface-muted text-muted-strong",
};

export function SystemHealthView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["admin", "system", "health"], queryFn: fetchSystemHealth });
  const [message, setMessage] = useState("BetPulse admin test alert");
  const alert = useMutation({
    mutationFn: sendTestAlert,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin", "system", "health"] }),
  });

  return (
    <section className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-foreground">{t("admin.system.title")}</h1>
          <p className="text-sm text-muted">{t("admin.system.subtitle")}</p>
        </div>
        {health.data ? (
          <span className={cn("rounded-pill px-3 py-1 text-sm font-semibold", STATUS_CLASS[health.data.status])}>
            {t(`admin.system.status.${health.data.status}`)}
          </span>
        ) : null}
      </div>

      {health.isPending ? (
        <div className="h-32 rounded-card bg-surface-muted" aria-busy="true" />
      ) : health.data ? (
        <div className="grid gap-4 sm:grid-cols-3">
          {health.data.components.map((component) => (
            <article key={component.name} className="rounded-card border border-border bg-surface p-4 shadow-card">
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="font-bold text-foreground">{component.name}</h2>
                <span className={cn("rounded-pill px-2 py-1 text-xs font-semibold", STATUS_CLASS[component.status])}>
                  {t(`admin.system.status.${component.status}`)}
                </span>
              </div>
              <p className="text-sm text-muted-strong">{component.detail ?? "—"}</p>
              <p className="mt-2 text-xs text-muted">
                {t("admin.system.latency")}: {component.latency_ms ?? "—"}ms
              </p>
              {Object.keys(component.meta).length ? (
                <dl className="mt-3 space-y-1 text-xs text-muted">
                  {Object.entries(component.meta).map(([key, value]) => (
                    <div key={key} className="flex justify-between gap-3">
                      <dt className="font-medium text-muted-strong">{key}</dt>
                      <dd className="truncate text-right">{formatMetaValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        <p className="rounded-card border border-border bg-surface p-4 text-sm text-warn">{t("admin.system.error")}</p>
      )}

      <form
        className="rounded-card border border-border bg-surface p-4 shadow-card"
        onSubmit={(event) => {
          event.preventDefault();
          alert.mutate(message);
        }}
      >
        <h2 className="mb-3 text-sm font-semibold text-muted-strong">{t("admin.system.testAlert")}</h2>
        <label className="mb-3 flex flex-col gap-1 text-sm">
          {t("admin.system.message")}
          <textarea
            className="min-h-20 rounded-md border border-border px-3 py-2"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
        </label>
        <button
          type="submit"
          className="rounded-pill bg-brand px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
          disabled={!message.trim() || alert.isPending}
        >
          {t("admin.system.send")}
        </button>
        {alert.data ? (
          <p className="mt-3 text-sm text-muted-strong">
            {alert.data.status === "sent" ? t("admin.system.sent") : t("admin.system.notConfigured")}
          </p>
        ) : null}
      </form>
    </section>
  );
}
