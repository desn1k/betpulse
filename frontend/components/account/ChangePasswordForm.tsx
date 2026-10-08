"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { TotpCodeInput } from "@/components/auth/TotpCodeInput";
import { Button } from "@/components/ui/Button";
import { AccountError, changePassword, waitMinutes } from "@/lib/auth/account";
import { LoginError, useAuthStore } from "@/lib/auth/store";

const MIN_LENGTH = 12; // the server's PasswordStr minimum

const inputClass =
  "rounded-md border border-border bg-surface px-3 py-2 outline-none focus:border-brand";

/**
 * Password change, then an automatic sign-in with the new password: the server
 * revokes every refresh token on a change, so without it the session would end
 * at the next renewal. An account with TOTP on is asked for its code to finish.
 */
export function ChangePasswordForm({ onChanged }: { onChanged?: () => void }) {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const login = useAuthStore((s) => s.login);
  const logout = useAuthStore((s) => s.logout);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  // Set once the server accepted the change: the new password for the re-sign-in.
  const [changedTo, setChangedTo] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!user) return null;
  const email = user.email;

  async function signInAgain(password: string, totpCode?: string) {
    try {
      await login(email, password, totpCode);
      setDone(true);
      setCode(null);
      setChangedTo(null);
      setCurrent("");
      setNext("");
      setRepeat("");
      onChanged?.();
    } catch (err) {
      if (err instanceof LoginError && err.reason === "totp_required") {
        setCode("");
      } else if (err instanceof LoginError && err.reason === "invalid_credentials" && totpCode) {
        setError(t("auth.totpInvalid"));
      } else if (err instanceof LoginError && err.reason === "rate_limited") {
        setError(t("auth.tooManyAttempts", { minutes: waitMinutes(err.retryAfterSeconds) }));
      } else {
        setError(t("security.reloginFailed"));
        await logout();
      }
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(false);
    setBusy(true);
    try {
      if (changedTo !== null) {
        await signInAgain(changedTo, code ?? undefined);
        return;
      }
      if (next.length < MIN_LENGTH) {
        setError(t("security.passwordTooShort"));
        return;
      }
      if (next !== repeat) {
        setError(t("security.passwordMismatch"));
        return;
      }
      try {
        await changePassword(current, next);
      } catch (err) {
        if (err instanceof AccountError && err.status === 400) {
          setError(t("security.wrongCurrentPassword"));
        } else if (err instanceof AccountError && err.status === 429) {
          setError(t("auth.tooManyAttempts", { minutes: waitMinutes(err.retryAfterSeconds) }));
        } else {
          setError(t("security.failed"));
        }
        return;
      }
      setChangedTo(next);
      await signInAgain(next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label={t("security.passwordTitle")}>
      {changedTo === null ? (
        <>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-muted-strong">{t("security.currentPassword")}</span>
            <input
              name="current"
              type="password"
              required
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-muted-strong">{t("security.newPassword")}</span>
            <input
              name="next"
              type="password"
              required
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-muted-strong">{t("security.repeatPassword")}</span>
            <input
              name="repeat"
              type="password"
              required
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              className={inputClass}
            />
          </label>
        </>
      ) : (
        code !== null && (
          <>
            <p className="text-sm text-muted-strong">{t("security.reloginTotp")}</p>
            <TotpCodeInput value={code} onChange={setCode} autoFocus />
          </>
        )
      )}
      {error && (
        <p role="alert" className="text-sm text-live">
          {error}
        </p>
      )}
      {done && (
        <p role="status" className="text-sm text-brand-strong">
          {t("security.passwordChanged")}
        </p>
      )}
      <Button type="submit" disabled={busy}>
        {changedTo === null ? t("security.changePassword") : t("auth.login")}
      </Button>
    </form>
  );
}
