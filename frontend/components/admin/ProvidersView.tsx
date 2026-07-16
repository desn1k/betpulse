"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  createProvider,
  deleteProvider,
  fetchProviders,
  setProviderEnabled,
} from "@/lib/admin";
import type { ProviderRole } from "@/types/admin";

const ROLES: ProviderRole[] = ["historical", "live", "odds", "xg"];

const emptyForm = {
  name: "",
  roles: [] as string[],
  priority: 100,
  api_key: "",
  requests_per_day: "",
};

export function ProvidersView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(emptyForm);

  const providers = useQuery({ queryKey: ["admin", "providers"], queryFn: fetchProviders });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", "providers"] });

  const create = useMutation({
    mutationFn: () =>
      createProvider({
        name: form.name,
        roles: form.roles,
        priority: form.priority,
        api_key: form.api_key || undefined,
        requests_per_day: form.requests_per_day ? Number(form.requests_per_day) : null,
      }),
    onSuccess: async () => {
      setForm(emptyForm);
      await invalidate();
    },
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setProviderEnabled(id, enabled),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: deleteProvider, onSuccess: invalidate });

  function toggleRole(role: string) {
    setForm((f) => ({
      ...f,
      roles: f.roles.includes(role) ? f.roles.filter((r) => r !== role) : [...f.roles, role],
    }));
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-extrabold text-foreground">{t("admin.providers.title")}</h1>

      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-sm font-semibold text-muted-strong">{t("admin.providers.add")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.providers.name")}
            <input
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.providers.apiKey")}
            <input
              type="password"
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.api_key}
              onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.providers.priority")}
            <input
              type="number"
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.priority}
              onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.providers.requestsPerDay")}
            <input
              type="number"
              className="rounded-md border border-border bg-surface px-3 py-2"
              value={form.requests_per_day}
              onChange={(e) => setForm((f) => ({ ...f, requests_per_day: e.target.value }))}
            />
          </label>
        </div>
        <fieldset className="flex flex-wrap gap-3">
          <legend className="sr-only">{t("admin.providers.roles")}</legend>
          {ROLES.map((role) => (
            <label key={role} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={form.roles.includes(role)}
                onChange={() => toggleRole(role)}
              />
              {role}
            </label>
          ))}
        </fieldset>
        <div>
          <Button
            onClick={() => create.mutate()}
            disabled={!form.name || form.roles.length === 0 || create.isPending}
          >
            {t("admin.providers.save")}
          </Button>
        </div>
      </Card>

      {providers.isPending ? (
        <div className="h-24 rounded-card bg-surface-muted" aria-busy="true" />
      ) : providers.data && providers.data.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">{t("admin.providers.name")}</th>
                <th className="px-3 py-2">{t("admin.providers.roles")}</th>
                <th className="px-3 py-2">{t("admin.providers.priority")}</th>
                <th className="px-3 py-2">{t("admin.providers.key")}</th>
                <th className="px-3 py-2">{t("admin.providers.enabled")}</th>
                <th className="px-3 py-2">{t("admin.providers.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {providers.data.map((p) => (
                <tr key={p.id} className="border-t border-border">
                  <td className="px-3 py-2 font-semibold text-foreground">{p.name}</td>
                  <td className="px-3 py-2 text-muted-strong">{p.roles.join(", ")}</td>
                  <td className="px-3 py-2 tabular-nums">{p.priority}</td>
                  <td className="px-3 py-2 tabular-nums text-muted">{p.key_masked ?? "—"}</td>
                  <td className="px-3 py-2">{p.is_enabled ? "✅" : "⛔"}</td>
                  <td className="flex gap-2 px-3 py-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => toggle.mutate({ id: p.id, enabled: !p.is_enabled })}
                    >
                      {p.is_enabled ? t("admin.providers.disable") : t("admin.providers.enable")}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => remove.mutate(p.id)}>
                      {t("admin.providers.delete")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-muted">{t("admin.providers.empty")}</p>
      )}
    </div>
  );
}
