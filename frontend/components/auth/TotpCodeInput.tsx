"use client";

import { useTranslations } from "next-intl";

/** The 6-digit TOTP code field, shared by sign-in, re-sign-in and TOTP setup. */
export function TotpCodeInput({
  value,
  onChange,
  autoFocus = false,
}: {
  value: string;
  onChange: (value: string) => void;
  autoFocus?: boolean;
}) {
  const t = useTranslations();
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium text-muted-strong">{t("auth.totpCode")}</span>
      <input
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]{6}"
        maxLength={6}
        required
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
        className="rounded-md border border-border bg-surface px-3 py-2 font-mono tracking-widest outline-none focus:border-brand"
      />
    </label>
  );
}
