"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

const STORAGE_KEY = "bp_disclaimer_collapsed";

/**
 * Persistent inline disclaimer on the match detail page (spec §19). Not a modal
 * or toast — an in-flow element the user can collapse; the collapsed state is
 * remembered for the session via sessionStorage, so it stays collapsed while
 * they browse but returns on a fresh session.
 */
export function DisclaimerBanner() {
  const t = useTranslations();
  // Start expanded on the server; reconcile with sessionStorage after mount to
  // avoid a hydration mismatch.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(sessionStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  function update(next: boolean) {
    setCollapsed(next);
    sessionStorage.setItem(STORAGE_KEY, next ? "1" : "0");
  }

  return (
    <aside
      className="rounded-card border border-border bg-surface-muted p-4 text-sm text-muted-strong"
      aria-label={t("disclaimer.label")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-foreground">{t("disclaimer.label")}</span>
        <button
          type="button"
          onClick={() => update(!collapsed)}
          className="text-xs font-semibold text-brand"
          aria-expanded={!collapsed}
        >
          {collapsed ? t("disclaimer.expand") : t("disclaimer.collapse")}
        </button>
      </div>
      {!collapsed && <p className="mt-2 leading-relaxed">{t("disclaimer.text")}</p>}
    </aside>
  );
}
