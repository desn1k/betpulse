"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { RedeemPromo } from "@/components/promo/RedeemPromo";
import { useAuthStore } from "@/lib/auth/store";
import { matchKeys } from "@/lib/queries";

import { LoginForm } from "./LoginForm";

/** Header auth control: a login popover when signed out, email + logout when in.
 * Until the page-load session restore settles it shows a neutral placeholder of
 * the same size, so a signed-in reload never flashes "Sign in" (F14). */
export function AuthMenu() {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const logout = useAuthStore((s) => s.logout);
  const sessionExpired = useAuthStore((s) => s.sessionExpired);
  const refreshFailing = useAuthStore((s) => s.refreshFailing);
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [promoOpen, setPromoOpen] = useState(false);

  async function onLogout() {
    await logout();
    await queryClient.invalidateQueries({ queryKey: matchKeys.all });
  }

  if (!hydrated && !user) {
    return <div aria-busy="true" className="h-9 w-20 rounded-md bg-surface-muted" />;
  }

  if (user) {
    return (
      <div className="flex items-center gap-2">
        {refreshFailing && (
          <span className="max-w-[16rem] text-xs text-muted-strong" role="status">
            {t("auth.sessionRefreshFailing")}
          </span>
        )}
        <span className="hidden max-w-[12rem] truncate text-sm text-muted-strong sm:inline">
          {user.email}
        </span>
        <Link
          href="/account/security"
          className="text-sm font-semibold text-muted-strong hover:text-foreground"
        >
          {t("auth.securityLink")}
        </Link>
        {user.role === "admin" && (
          <Link
            href="/admin"
            className="text-sm font-semibold text-muted-strong hover:text-foreground"
          >
            {t("security.adminPanel")}
          </Link>
        )}
        <div className="relative">
          <Button size="sm" variant="ghost" onClick={() => setPromoOpen((v) => !v)} aria-expanded={promoOpen}>
            {t("promo.submit")}
          </Button>
          {promoOpen && (
            <Card className="absolute right-0 top-11 z-40 w-72 p-4">
              <RedeemPromo />
            </Card>
          )}
        </div>
        <Button size="sm" variant="secondary" onClick={onLogout}>
          {t("auth.logout")}
        </Button>
      </div>
    );
  }

  return (
    <div className="relative flex items-center gap-2">
      {sessionExpired && (
        <span className="max-w-[16rem] text-xs text-muted-strong" role="status">
          {t("auth.sessionExpired")}
        </span>
      )}
      <Button size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {t("auth.login")}
      </Button>
      {open && (
        <Card className="absolute right-0 top-11 z-40 w-72 p-4">
          <LoginForm onDone={() => setOpen(false)} />
        </Card>
      )}
    </div>
  );
}
