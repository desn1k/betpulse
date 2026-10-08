"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { waitMinutes } from "@/lib/auth/account";
import { LoginError, useAuthStore } from "@/lib/auth/store";
import { matchKeys } from "@/lib/queries";

import { TotpCodeInput } from "./TotpCodeInput";

/**
 * Sign-in in one or two steps: email + password, then — only when the server
 * answers that the account has TOTP on — the 6-digit code. The password stays in
 * this component's state until the sign-in ends; it is never stored.
 */
export function LoginForm({ onDone }: { onDone?: () => void }) {
  const t = useTranslations();
  const login = useAuthStore((s) => s.login);
  const pending = useAuthStore((s) => s.pending);
  const queryClient = useQueryClient();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password, code ?? undefined);
      // Refetch matches so they reflect the now-authenticated tier.
      await queryClient.invalidateQueries({ queryKey: matchKeys.all });
      onDone?.();
    } catch (err) {
      if (!(err instanceof LoginError)) {
        setError(t("auth.loginFailed"));
      } else if (err.reason === "totp_required") {
        setCode("");
      } else if (err.reason === "invalid_credentials") {
        // On the code step the password was right: the server answers a wrong
        // code exactly like a wrong password, the form knows which step it is.
        setError(code === null ? t("auth.invalidCredentials") : t("auth.totpInvalid"));
      } else if (err.reason === "rate_limited") {
        setError(t("auth.tooManyAttempts", { minutes: waitMinutes(err.retryAfterSeconds) }));
      } else {
        setError(t("auth.loginFailed"));
      }
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label={t("auth.login")}>
      {code === null ? (
        <>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-muted-strong">{t("auth.email")}</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-border bg-surface px-3 py-2 outline-none focus:border-brand"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-muted-strong">{t("auth.password")}</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-md border border-border bg-surface px-3 py-2 outline-none focus:border-brand"
            />
          </label>
        </>
      ) : (
        <>
          <p className="text-sm text-muted-strong">{t("auth.totpHint")}</p>
          <TotpCodeInput value={code} onChange={setCode} autoFocus />
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-live">
          {error}
        </p>
      )}
      <Button type="submit" disabled={pending}>
        {pending ? t("auth.signingIn") : t("auth.login")}
      </Button>
    </form>
  );
}
