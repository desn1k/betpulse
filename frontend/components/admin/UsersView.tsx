"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  assignTier,
  fetchRedemptions,
  fetchTiers,
  fetchUsers,
  setUserActive,
} from "@/lib/admin";
import type { Redemption } from "@/types/admin";

const TIER_FILTERS = ["free", "pro", "expert"] as const;

export function UsersView() {
  const t = useTranslations();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [search, setSearch] = useState("");
  const [tier, setTier] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [redemptions, setRedemptions] = useState<Record<string, Redemption[]>>({});
  const [assign, setAssign] = useState<Record<string, { tier_id: string; expires_at: string }>>({});

  const users = useQuery({
    queryKey: ["admin", "users", search, tier],
    queryFn: () => fetchUsers({ email: search || undefined, tier: tier || undefined }),
  });
  const tiers = useQuery({ queryKey: ["admin", "tiers"], queryFn: fetchTiers });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", "users"] });

  const toggleActive = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => setUserActive(id, active),
    onSuccess: invalidate,
  });
  const grant = useMutation({
    mutationFn: ({ id, tier_id, expires_at }: { id: string; tier_id: string; expires_at: string }) =>
      assignTier(id, { tier_id, expires_at: expires_at || null }),
    onSuccess: invalidate,
  });

  async function loadRedemptions(id: string) {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (!redemptions[id]) {
      const rows = await fetchRedemptions(id);
      setRedemptions((r) => ({ ...r, [id]: rows }));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-extrabold text-foreground">{t("admin.users.title")}</h1>

      {/* Filters */}
      <Card className="flex flex-wrap items-end gap-3 p-4">
        <label className="flex flex-col gap-1 text-sm">
          {t("admin.users.email")}
          <input
            className="rounded-md border border-border bg-surface px-3 py-2"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setSearch(email)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("admin.users.tier")}
          <select
            aria-label={t("admin.users.tier")}
            className="rounded-md border border-border bg-surface px-3 py-2"
            value={tier}
            onChange={(e) => setTier(e.target.value)}
          >
            <option value="">{t("admin.users.allTiers")}</option>
            {TIER_FILTERS.map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </label>
        <Button size="sm" onClick={() => setSearch(email)}>
          {t("admin.users.search")}
        </Button>
      </Card>

      {users.isPending ? (
        <div className="h-40 rounded-card bg-surface-muted" aria-busy="true" />
      ) : users.data && users.data.users.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-2 py-2">{t("admin.users.email")}</th>
                <th className="px-2 py-2">{t("admin.users.tier")}</th>
                <th className="px-2 py-2">{t("admin.users.expires")}</th>
                <th className="px-2 py-2">{t("admin.users.status")}</th>
                <th className="px-2 py-2">{t("admin.users.assign")}</th>
                <th className="px-2 py-2">{t("admin.users.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {users.data.users.map((u) => {
                const a = assign[u.id] ?? { tier_id: "", expires_at: "" };
                return (
                  <Fragment key={u.id}>
                    <tr className="border-t border-border">
                      <td className="px-2 py-2 font-semibold text-foreground">{u.email}</td>
                      <td className="px-2 py-2">
                        <Badge variant={u.effective_tier === "free" ? "neutral" : "brand"}>
                          {u.effective_tier}
                        </Badge>
                      </td>
                      <td className="px-2 py-2 text-muted">
                        {u.tier_expires_at ? u.tier_expires_at.slice(0, 10) : "—"}
                      </td>
                      <td className="px-2 py-2">
                        {u.is_active ? (
                          <span className="text-live">●</span>
                        ) : (
                          <Badge variant="warn">{t("admin.users.disabled")}</Badge>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <div className="flex items-center gap-1">
                          <select
                            aria-label={`${u.email} tier`}
                            className="rounded border border-border bg-surface px-1 py-1 text-xs"
                            value={a.tier_id}
                            onChange={(e) =>
                              setAssign((s) => ({
                                ...s,
                                [u.id]: { ...a, tier_id: e.target.value },
                              }))
                            }
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
                          <input
                            type="date"
                            aria-label={`${u.email} expires`}
                            className="rounded border border-border bg-surface px-1 py-1 text-xs"
                            value={a.expires_at}
                            onChange={(e) =>
                              setAssign((s) => ({
                                ...s,
                                [u.id]: { ...a, expires_at: e.target.value },
                              }))
                            }
                          />
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={!a.tier_id || grant.isPending}
                            onClick={() =>
                              grant.mutate({
                                id: u.id,
                                tier_id: a.tier_id,
                                expires_at: a.expires_at,
                              })
                            }
                          >
                            {t("admin.users.grant")}
                          </Button>
                        </div>
                      </td>
                      <td className="flex gap-1 px-2 py-2">
                        <Button variant="ghost" size="sm" onClick={() => loadRedemptions(u.id)}>
                          {t("admin.users.redemptions")}
                        </Button>
                        {u.is_active ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => toggleActive.mutate({ id: u.id, active: false })}
                          >
                            {t("admin.users.disable")}
                          </Button>
                        ) : (
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => toggleActive.mutate({ id: u.id, active: true })}
                          >
                            {t("admin.users.enable")}
                          </Button>
                        )}
                      </td>
                    </tr>
                    {expanded === u.id && (
                      <tr className="border-t border-border bg-surface-muted">
                        <td colSpan={6} className="px-4 py-2 text-xs">
                          {redemptions[u.id]?.length ? (
                            <ul className="flex flex-col gap-1">
                              {redemptions[u.id].map((r) => (
                                <li key={r.id} className="text-muted-strong">
                                  {r.code_type}
                                  {r.value ? ` (${r.value})` : ""} · {r.status} ·{" "}
                                  {r.redeemed_at.slice(0, 10)}
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <span className="text-muted">{t("admin.users.noRedemptions")}</span>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-muted">
            {t("admin.users.total", { total: users.data.total })}
          </p>
        </div>
      ) : (
        <p className="text-sm text-muted">{t("admin.users.empty")}</p>
      )}
    </div>
  );
}
