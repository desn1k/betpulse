"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useAuthStore } from "@/lib/auth/store";
import { matchKeys } from "@/lib/queries";

import { LoginForm } from "./LoginForm";

/** Header auth control: a login popover when signed out, email + logout when in. */
export function AuthMenu() {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  async function onLogout() {
    await logout();
    await queryClient.invalidateQueries({ queryKey: matchKeys.all });
  }

  if (user) {
    return (
      <div className="flex items-center gap-2">
        <span className="hidden max-w-[12rem] truncate text-sm text-muted-strong sm:inline">
          {user.email}
        </span>
        <Button size="sm" variant="secondary" onClick={onLogout}>
          {t("auth.logout")}
        </Button>
      </div>
    );
  }

  return (
    <div className="relative">
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
