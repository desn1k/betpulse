"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, type ReactNode } from "react";

import { useAuthStore } from "@/lib/auth/store";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/admin/providers", key: "providers" },
  { href: "/admin/ingestion", key: "ingestion" },
  { href: "/admin/models", key: "models" },
  { href: "/admin/spend", key: "spend" },
  { href: "/admin/users", key: "users" },
  { href: "/admin/promo", key: "promo" },
  { href: "/admin/tiers", key: "tiers" },
] as const;

export default function AdminLayout({ children }: { children: ReactNode }) {
  const t = useTranslations();
  const router = useRouter();
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);

  const isAdmin = user?.role === "admin";

  // Wait for the silent-refresh to settle, then bounce non-admins. The backend
  // enforces RBAC regardless; this is the UX guard.
  useEffect(() => {
    if (hydrated && !isAdmin) router.replace("/");
  }, [hydrated, isAdmin, router]);

  if (!hydrated) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8" aria-busy="true">
        <div className="h-8 w-40 rounded bg-surface-muted" />
      </div>
    );
  }
  if (!isAdmin) return null;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8 sm:flex-row">
      <aside className="sm:w-48 sm:flex-shrink-0">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("admin.title")}
        </h2>
        <nav className="flex flex-row gap-1 sm:flex-col">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rounded-md px-3 py-2 text-sm font-semibold transition",
                pathname === item.href
                  ? "bg-brand-soft text-brand-strong"
                  : "text-muted-strong hover:bg-surface-muted hover:text-foreground",
              )}
            >
              {t(`admin.nav.${item.key}`)}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
