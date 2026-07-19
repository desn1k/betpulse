"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { fetchAudit } from "@/lib/admin";

export function AuditView() {
  const t = useTranslations();
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const audit = useQuery({
    queryKey: ["admin", "audit", { q, action, page }],
    queryFn: () => fetchAudit({ q, action, page }),
  });

  const events = audit.data?.events ?? [];
  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-extrabold text-foreground">{t("admin.audit.title")}</h1>
        <p className="text-sm text-muted">{t("admin.audit.subtitle")}</p>
      </div>

      <div className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4 shadow-card sm:flex-row">
        <label className="flex flex-1 flex-col gap-1 text-sm">
          {t("admin.audit.search")}
          <input
            className="rounded-md border border-border px-3 py-2"
            value={q}
            onChange={(event) => {
              setQ(event.target.value);
              setPage(1);
            }}
          />
        </label>
        <label className="flex flex-1 flex-col gap-1 text-sm">
          {t("admin.audit.action")}
          <input
            className="rounded-md border border-border px-3 py-2"
            value={action}
            onChange={(event) => {
              setAction(event.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>

      <div className="overflow-x-auto rounded-card border border-border bg-surface shadow-card">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-surface-muted text-xs uppercase text-muted">
            <tr>
              <th className="px-3 py-2">{t("admin.audit.time")}</th>
              <th className="px-3 py-2">{t("admin.audit.actor")}</th>
              <th className="px-3 py-2">{t("admin.audit.action")}</th>
              <th className="px-3 py-2">{t("admin.audit.target")}</th>
              <th className="px-3 py-2">{t("admin.audit.meta")}</th>
            </tr>
          </thead>
          <tbody>
            {audit.isPending ? (
              <tr><td className="px-3 py-6 text-muted" colSpan={5}>{t("admin.audit.loading")}</td></tr>
            ) : events.length ? (
              events.map((event) => (
                <tr key={event.id} className="border-t border-border align-top">
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{new Date(event.created_at).toLocaleString()}</td>
                  <td className="px-3 py-2">{event.actor_email ?? event.actor_user_id ?? "system"}</td>
                  <td className="px-3 py-2 font-semibold">{event.action}</td>
                  <td className="px-3 py-2 text-muted-strong">{event.target ?? "—"}</td>
                  <td className="max-w-sm px-3 py-2 font-mono text-xs text-muted">{JSON.stringify(event.meta)}</td>
                </tr>
              ))
            ) : (
              <tr><td className="px-3 py-6 text-muted" colSpan={5}>{t("admin.audit.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-muted-strong">
        <span>{t("admin.audit.total", { total: audit.data?.total ?? 0 })}</span>
        <div className="flex gap-2">
          <button className="rounded-md border border-border px-3 py-1 disabled:opacity-50" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            {t("admin.audit.prev")}
          </button>
          <button className="rounded-md border border-border px-3 py-1 disabled:opacity-50" disabled={!audit.data || page * audit.data.per_page >= audit.data.total} onClick={() => setPage((p) => p + 1)}>
            {t("admin.audit.next")}
          </button>
        </div>
      </div>
    </section>
  );
}
