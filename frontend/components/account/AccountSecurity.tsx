"use client";

import { useTranslations } from "next-intl";

import { Card } from "@/components/ui/Card";
import { useAuthStore } from "@/lib/auth/store";

import { ChangePasswordForm } from "./ChangePasswordForm";
import { TwoFactorSection } from "./TwoFactorSection";

/** Password and two-factor settings for any signed-in user; the admin panel
 * sends an admin here until the initial password is changed and, when the
 * server requires it, TOTP is on (F10). */
export function AccountSecurity() {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);

  if (!hydrated) {
    return <div aria-busy="true" className="h-40 rounded-card bg-surface-muted" />;
  }
  if (!user) {
    return <p className="text-muted-strong">{t("security.signInRequired")}</p>;
  }

  return (
    <>
      {user.must_change_password && (
        <p role="note" className="rounded-card bg-warn/10 p-3 text-sm text-warn">
          {t("security.mustChangePassword")}
        </p>
      )}
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold text-foreground">{t("security.passwordTitle")}</h2>
        <ChangePasswordForm />
      </Card>
      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-lg font-bold text-foreground">{t("security.totpTitle")}</h2>
        <TwoFactorSection />
      </Card>
    </>
  );
}
